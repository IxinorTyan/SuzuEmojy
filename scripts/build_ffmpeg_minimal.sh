#!/usr/bin/env bash
# Build the Windows x64, offline sticker converter used by the Nuitka release.
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
downloads="$repo/dist/ffmpeg-minimal-downloads"
out="$repo/dist/ffmpeg-minimal"
jobs=${1:-4}
[[ $jobs =~ ^[1-9][0-9]*$ && $# -le 1 ]] || { echo "Usage: $0 [jobs]" >&2; exit 2; }
for tool in cygpath curl tar sha256sum make gcc nasm objdump; do
    command -v "$tool" >/dev/null || { echo "Missing build tool: $tool (see docs/ffmpeg-minimal.md)" >&2; exit 1; }
done
[[ $(gcc -dumpmachine) == x86_64-w64-mingw32 ]] || { echo "Use an x64 MinGW-w64 GCC." >&2; exit 1; }

ffmpeg=ffmpeg-8.1.3
vpx=libvpx-1.17.0
zlib=zlib-1.4
mkdir -p "$downloads" "$out"

fetch() {
    local file=$1 hash=$2 url=$3
    if [[ ! -f $downloads/$file ]]; then
        curl -fL --silent --show-error --retry 2 --connect-timeout 15 --max-time 180 \
            "$url" -o "$downloads/$file.partial"
        printf '%s  %s\n' "$hash" "$downloads/$file.partial" | sha256sum -c -
        mv -f "$downloads/$file.partial" "$downloads/$file"
    fi
    printf '%s  %s\n' "$hash" "$downloads/$file" | sha256sum -c -
}

fetch "$ffmpeg.tar.xz" 7138d28c96d9d3e3af4ee3d8cad72741f8ffb40da90c1112235dea3ecd3178a3 \
    "https://ffmpeg.org/releases/$ffmpeg.tar.xz"
fetch "$vpx.tar.gz" 1020f184046187baa2985dbde38e0691f49c44088bca7a1842b0236c6081dc0a \
    "https://codeload.github.com/webmproject/libvpx/tar.gz/refs/tags/v${vpx#libvpx-}"
fetch "$zlib.tar.xz" 4f11c512053d7bab8d9e9b89bde3575add3e4c50a383b42ef6cb837035fc5a7c \
    "https://github.com/madler/zlib/releases/download/v${zlib#zlib-}/$zlib.tar.xz"

# Source and compiler work stay outside the project path (which may contain spaces).
# Keep the unique directory for inspection if configure or a build fails.
work=$(mktemp -d /tmp/suzu-ffmpeg.XXXXXXXX)
echo "FFmpeg build directory: $work ($(cygpath -w "$work"))"
cd "$work"
mkdir -p snapshot/{scripts,docs}
cp "$repo/scripts/build_ffmpeg_minimal.sh" snapshot/scripts/
cp "$repo/docs/ffmpeg-minimal.md" snapshot/docs/
tar -xf "$downloads/$ffmpeg.tar.xz"
tar -xf "$downloads/$vpx.tar.gz"
tar -xf "$downloads/$zlib.tar.xz"
mkdir -p prefix/{lib,include}
prefix=$(cygpath -m "$work/prefix")

echo "Building zlib..."
(
    cd "$work/$zlib"
    make -f win32/Makefile.gcc -j "$jobs" CFLAGS="-Os -DNDEBUG" libz.a
    cp libz.a "$work/prefix/lib/"
    cp zlib.h zconf.h "$work/prefix/include/"
)
echo "Building libvpx (VP9 decoder only)..."
(
    cd "$work/$vpx"
    ./configure --target=x86_64-win64-gcc --prefix="$work/prefix" \
        --disable-examples --disable-tools --disable-docs --disable-unit-tests \
        --disable-vp8 --disable-vp9-encoder --enable-vp9-highbitdepth --disable-avx512
    make -j "$jobs"
    make install
)
echo "Building FFmpeg..."
(
    cd "$work/$ffmpeg"
    ./configure --target-os=mingw32 --arch=x86_64 --pkg-config=false \
        --disable-autodetect --disable-everything --disable-network \
        --disable-doc --disable-ffplay --disable-ffprobe --disable-avdevice \
        --enable-small --disable-debug --disable-shared --enable-static \
        --disable-avx512 --disable-avx512icl \
        --extra-cflags="-I$prefix/include" --extra-ldflags="-L$prefix/lib -static" \
        --enable-zlib --enable-libvpx --enable-decoder=libvpx_vp9 \
        --enable-demuxer=matroska --enable-muxer=gif,image2 --enable-encoder=gif,png \
        --enable-filter=format,fps,scale,split,palettegen,paletteuse \
        --enable-protocol=file,pipe
    make -j "$jobs" ffmpeg.exe
)

bin="$work/$ffmpeg/ffmpeg.exe"
"$bin" -version
imports=$(objdump -p "$bin" | sed -n 's/.*DLL Name: //p')
printf 'Windows imports:\n%s\n' "$imports"
if [[ -z $imports ]] || grep -Ei 'msys|cygwin|lib(winpthread|gcc|stdc\+\+|vpx)|zlib' <<< "$imports"; then
    echo "FFmpeg is expected to run without third-party DLLs." >&2
    exit 1
fi

notice="$work/THIRD_PARTY.txt"
{
    printf 'SuzuEmojy uses FFmpeg %s (LGPL 2.1 or later), libvpx %s, and zlib %s.\n' \
        "${ffmpeg#ffmpeg-}" "${vpx#libvpx-}" "${zlib#zlib-}"
    printf 'Corresponding source and the build script: SuzuEmojy-FFmpeg-sources.tar, alongside the app download.\n'
    printf 'https://github.com/IxinorTyan/SuzuEmojy/releases\n'
    for file in "$ffmpeg/COPYING.LGPLv2.1" "$vpx/LICENSE" "$vpx/PATENTS" "$zlib/LICENSE"; do
        printf '\n--- %s ---\n' "$file"
        cat "$work/$file"
    done
} > "$notice"
tar -cf "$work/SuzuEmojy-FFmpeg-sources.tar" \
    -C "$repo" "dist/ffmpeg-minimal-downloads/$ffmpeg.tar.xz" \
    "dist/ffmpeg-minimal-downloads/$vpx.tar.gz" "dist/ffmpeg-minimal-downloads/$zlib.tar.xz" \
    -C "$work/snapshot" scripts/build_ffmpeg_minimal.sh docs/ffmpeg-minimal.md \
    -C "$work/$ffmpeg" config.h
cmp "$repo/scripts/build_ffmpeg_minimal.sh" "$work/snapshot/scripts/build_ffmpeg_minimal.sh"
sha256sum "$work/snapshot/scripts/build_ffmpeg_minimal.sh" | cut -d ' ' -f 1 > "$work/recipe.sha256"
cp "$notice" "$out/THIRD_PARTY.txt"
cp "$work/SuzuEmojy-FFmpeg-sources.tar" "$out/"
cp "$bin" "$out/ffmpeg.exe"
cp "$work/recipe.sha256" "$out/"
echo "Ready: $out/ffmpeg.exe ($(wc -c < "$out/ffmpeg.exe") bytes)"
echo "Build intermediates: $(cygpath -w "$work")"
