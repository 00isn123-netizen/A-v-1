FROM archlinux:latest

# 1. Base packages (includes vapoursynth, ffms2, compiler tools, AND cmake)
RUN pacman -Syu --noconfirm && \
    pacman -S --noconfirm gcc make wget pv git bash xz gawk \
    python python-pip mediainfo psmisc procps-ng supervisor \
    zlib bzip2 readline sqlite openssl libffi \
    findutils gdbm ncurses tar curl \
    aria2 base-devel tk \
    rust nasm clang vapoursynth \
    autoconf automake libtool perl cmake \
    aom ffms2 libvpx mkvtoolnix-cli vmaf meson ninja && \
    pacman -Scc --noconfirm

# 2. Build SVT-AV1
RUN git clone --depth 1 https://gitlab.com/AOMediaCodec/SVT-AV1.git /tmp/SVT-AV1 && \
    cd /tmp/SVT-AV1/Build && \
    cmake .. -G"Unix Makefiles" -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF && \
    make -j$(nproc) && \
    make install && \
    rm -rf /tmp/SVT-AV1

# 3. Pyenv setup
ENV PYENV_ROOT="/root/.pyenv"
ENV PATH="$PYENV_ROOT/bin:$PYENV_ROOT/shims:$PATH"

RUN bash -c ' \
    export PYENV_ROOT="/root/.pyenv" && \
    export PATH="$PYENV_ROOT/bin:$PYENV_ROOT/shims:$PATH" && \
    git clone https://github.com/pyenv/pyenv.git $PYENV_ROOT && \
    git clone https://github.com/pyenv/pyenv-virtualenv.git $PYENV_ROOT/plugins/pyenv-virtualenv && \
    eval "$(pyenv init -)" && \
    eval "$(pyenv virtualenv-init -)" && \
    pyenv install 3.8.18 && \
    pyenv install 3.9.18 && \
    pyenv install 3.10.14 && \
    pyenv install 3.11.9 && \
    pyenv install 3.12.3 && \
    pyenv install 3.13.3 && \
    pyenv global 3.10.14 && \
    echo "eval \"\$(${PYENV_ROOT}/bin/pyenv init -)\"" >> /root/.bashrc && \
    echo "eval \"\$(${PYENV_ROOT}/bin/pyenv virtualenv-init -)\"" >> /root/.bashrc \
    '

# 4. VapourSynth Plugins
RUN git clone --depth 1 https://github.com/dubhater/vapoursynth-mvtools.git /tmp/mvtools && \
    cd /tmp/mvtools && \
    mkdir build && cd build && \
    meson setup .. --buildtype=release && \
    ninja && ninja install && \
    rm -rf /tmp/mvtools

RUN git clone --depth 1 https://github.com/HomeOfVapourSynthEvolution/VapourSynth-KNLMeansCL.git /tmp/knlm && \
    cd /tmp/knlm && \
    mkdir build && cd build && \
    cmake .. -DCMAKE_BUILD_TYPE=Release -DENABLE_OPENCL=OFF && \
    make -j$(nproc) && make install && \
    rm -rf /tmp/knlm

RUN git clone --depth 1 https://github.com/HomeOfVapourSynthEvolution/VapourSynth-descale.git /tmp/descale && \
    cd /tmp/descale && \
    mkdir build && cd build && \
    meson setup .. --buildtype=release && \
    ninja && ninja install && \
    rm -rf /tmp/descale

RUN git clone --depth 1 https://github.com/HomeOfVapourSynthEvolution/VapourSynth-NNEDI3.git /tmp/nnedi3 && \
    cd /tmp/nnedi3 && \
    mkdir build && cd build && \
    meson setup .. --buildtype=release && \
    ninja && ninja install && \
    rm -rf /tmp/nnedi3

RUN git clone --depth 1 https://github.com/HomeOfVapourSynthEvolution/neo_f3kdb.git /tmp/f3kdb && \
    cd /tmp/f3kdb && \
    mkdir build && cd build && \
    cmake .. -DCMAKE_BUILD_TYPE=Release && \
    make -j$(nproc) && make install && \
    rm -rf /tmp/f3kdb

# Python helpers
RUN pip install vsutil muvsfunc

RUN git clone --depth 1 https://github.com/HomeOfVapourSynthEvolution/havsfunc.git /tmp/havsfunc && \
    cp /tmp/havsfunc/havsfunc.py $(python -c "import site; print(site.getsitepackages()[0])")/havsfunc.py && \
    rm -rf /tmp/havsfunc

# 5. Build Av1an
RUN git clone https://github.com/master-of-zen/Av1an /tmp/Av1an && \
    cd /tmp/Av1an && \
    cargo build --release && \
    cp target/release/av1an /usr/local/bin/av1an && \
    rm -rf /tmp/Av1an

# 6. Final Setup & Copying files
ENV SUPERVISORD_CONF_DIR=/etc/supervisor/conf.d
ENV SUPERVISORD_LOG_DIR=/var/log/supervisor

RUN mkdir -p ${SUPERVISORD_CONF_DIR} \
    ${SUPERVISORD_LOG_DIR} \
    /app

WORKDIR /app

# Pull static FFmpeg binaries
COPY --from=mwader/static-ffmpeg:latest /ffmpeg /bin/ffmpeg
COPY --from=mwader/static-ffmpeg:latest /ffprobe /bin/ffprobe
COPY --from=mwader/static-ffmpeg:latest /doc /doc
COPY --from=mwader/static-ffmpeg:latest /versions.json /versions.json
COPY --from=mwader/static-ffmpeg:latest /etc/ssl/cert.pem /etc/ssl/cert.pem
COPY --from=mwader/static-ffmpeg:latest /etc/fonts /etc/fonts
COPY --from=mwader/static-ffmpeg:latest /usr/share/fonts /usr/share/fonts
COPY --from=mwader/static-ffmpeg:latest /usr/share/consolefonts /usr/share/consolefonts
COPY --from=mwader/static-ffmpeg:latest /var/cache/fontconfig /var/cache/fontconfig

# Copy local repository files into image
COPY . .
