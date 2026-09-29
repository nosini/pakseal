# The image cpak runs pakseal from. Build it with `make image`.
FROM docker.io/library/alpine:3.22

COPY tools/prune-image /usr/local/sbin/prune-image

# gtk4.0 depends on GStreamer, which pulls in Mesa and LLVM. Pakseal shows no
# video and renders with cairo, so those are deleted in the same layer.
RUN apk add --no-cache \
        python3 \
        py3-gobject3 \
        gtk4.0 \
        libadwaita \
        adwaita-icon-theme \
        font-cantarell \
        font-dejavu \
    && prune-image \
    && rm /usr/local/sbin/prune-image

COPY data/pakseal /usr/bin/pakseal
COPY pakseal/*.py pakseal/style.css /usr/lib/pakseal/pakseal/
COPY data/io.codeberg.nosini.Pakseal.desktop /usr/share/applications/
COPY data/io.codeberg.nosini.Pakseal.metainfo.xml /usr/share/metainfo/
COPY data/io.codeberg.nosini.Pakseal.svg /usr/share/icons/hicolor/scalable/apps/

RUN python3 -m compileall -q /usr/lib/pakseal
