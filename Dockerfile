# syntax=docker/dockerfile:1.7
# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — base-packages
#   All pacman installs live here. This layer is cached under its own registry
#   tag and is only rebuilt when the package list changes.
# ─────────────────────────────────────────────────────────────────────────────
FROM archlinux:latest AS base-packages

RUN --mount=type=cache,target=/var/cache/pacman/pkg,sharing=locked \
    pacman -Syu --noconfirm && \
    pacman -S --noconfirm \
        gcc make wget pv git bash xz gawk \
        python python-pip mediainfo psmisc procps-ng supervisor \
        zlib bzip2 readline sqlite openssl libffi \
        findutils gdbm ncurses tar curl \
        aria2 base-devel tk \
        rust nasm clang vapoursynth \
        autoconf automake libtool perl \
        aom ffms2 libvpx mkvtoolnix-cli vmaf \
        cmake

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — svt-av1
#   SVT-AV1 compiled from source. Only rebuilt when base-packages changes or
#   the cache tag is invalidated.
# ─────────────────────────────────────────────────────────────────────────────
FROM base-packages AS svt-av1

RUN --mount=type=cache,target=/root/.cargo/registry \
    git clone --depth 1 https://gitlab.com/AOMediaCodec/SVT-AV1.git /tmp/SVT-AV1 && \
    cd /tmp/SVT-AV1/Build && \
    cmake .. -G"Unix Makefiles" \
        -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_SHARED_LIBS=OFF && \
    make -j$(nproc) && \
    make install && \
    rm -rf /tmp/SVT-AV1

# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 — pyenv
#   Six Python versions via pyenv. Very slow to build; changes rarely.
# ─────────────────────────────────────────────────────────────────────────────
FROM svt-av1 AS pyenv

ENV PYENV_ROOT="/root/.pyenv"
ENV PATH="$PYENV_ROOT/bin:$PYENV_ROOT/shims:$PATH"

RUN --mount=type=cache,target=/tmp/pyenv-cache \
    git clone https://github.com/pyenv/pyenv.git $PYENV_ROOT && \
    git clone https://github.com/pyenv/pyenv-virtualenv.git \
        $PYENV_ROOT/plugins/pyenv-virtualenv && \
    eval "$(pyenv init -)" && \
    eval "$(pyenv virtualenv-init -)" && \
    pyenv install 3.8.18  && \
    pyenv install 3.9.18  && \
    pyenv install 3.10.14 && \
    pyenv install 3.11.9  && \
    pyenv install 3.12.3  && \
    pyenv install 3.13.3  && \
    pyenv global 3.10.14  && \
    echo 'eval "$(${PYENV_ROOT}/bin/pyenv init -)"'          >> /root/.bashrc && \
    echo 'eval "$(${PYENV_ROOT}/bin/pyenv virtualenv-init -)"' >> /root/.bashrc

# ─────────────────────────────────────────────────────────────────────────────
# Stage 4 — av1an
#   Rust build; uses a Cargo registry cache mount to avoid re-downloading
#   crates on every run.
# ─────────────────────────────────────────────────────────────────────────────
FROM pyenv AS av1an

RUN --mount=type=cache,target=/root/.cargo/registry \
    --mount=type=cache,target=/root/.cargo/git \
    --mount=type=cache,target=/tmp/av1an-target \
    git clone https://github.com/master-of-zen/Av1an /tmp/Av1an && \
    cd /tmp/Av1an && \
    # Symlink the shared cargo cache so the build lands in it
    CARGO_TARGET_DIR=/tmp/av1an-target \
    cargo build --release && \
    cp /tmp/av1an-target/release/av1an /usr/local/bin/av1an && \
    rm -rf /tmp/Av1an

# ─────────────────────────────────────────────────────────────────────────────
# Stage 5 — final
#   Thin finishing layer: supervisor dirs, app code, static ffmpeg binaries.
# ─────────────────────────────────────────────────────────────────────────────
FROM av1an AS final

ENV SUPERVISORD_CONF_DIR=/etc/supervisor/conf.d
ENV SUPERVISORD_LOG_DIR=/var/log/supervisor

RUN mkdir -p ${SUPERVISORD_CONF_DIR} ${SUPERVISORD_LOG_DIR} /app

WORKDIR /app

# Static ffmpeg — pulled from a separate image, never rebuilt
COPY --from=mwader/static-ffmpeg:latest /ffmpeg               /bin/ffmpeg
COPY --from=mwader/static-ffmpeg:latest /ffprobe              /bin/ffprobe
COPY --from=mwader/static-ffmpeg:latest /doc                  /doc
COPY --from=mwader/static-ffmpeg:latest /versions.json        /versions.json
COPY --from=mwader/static-ffmpeg:latest /etc/ssl/cert.pem     /etc/ssl/cert.pem
COPY --from=mwader/static-ffmpeg:latest /etc/fonts            /etc/fonts
COPY --from=mwader/static-ffmpeg:latest /usr/share/fonts      /usr/share/fonts
COPY --from=mwader/static-ffmpeg:latest /usr/share/consolefonts /usr/share/consolefonts
COPY --from=mwader/static-ffmpeg:latest /var/cache/fontconfig /var/cache/fontconfig

# App code — always the last COPY so a source change doesn't bust earlier layers
COPY . .
