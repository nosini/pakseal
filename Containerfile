# The image cpak runs pakseal from. Build it with `make image`.
FROM docker.io/library/alpine:3.22

RUN apk add --no-cache \
        python3 \
        py3-gobject3 \
        gtk4.0 \
        libadwaita \
        adwaita-icon-theme \
        font-cantarell \
        font-dejavu

COPY data/pakseal /usr/bin/pakseal
COPY pakseal/*.py pakseal/style.css /usr/lib/pakseal/pakseal/
COPY data/io.codeberg.nosini.Pakseal.desktop /usr/share/applications/
COPY data/io.codeberg.nosini.Pakseal.metainfo.xml /usr/share/metainfo/
COPY data/io.codeberg.nosini.Pakseal.svg /usr/share/icons/hicolor/scalable/apps/

RUN python3 -m compileall -q /usr/lib/pakseal
