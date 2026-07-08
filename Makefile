PJSIP_DIR=pjproject
PKG_CONFIG_PATH=pjsip.install/lib/pkgconfig

# Static build flags
STATIC_FLAGS := -static

ifndef NO_PULSE
    DM_LDFLAGS += -l pulse -l pulse-simple
    DM_CFLAGS += -DHAS_PULSE
endif

all: d-modem slmodemd $(if $(NO_PULSE),,d-modem.nopulse)

$(PKG_CONFIG_PATH)/libpjproject.pc:
	(cd $(PJSIP_DIR); [ -f ./config.status ] || ./configure --prefix=`pwd`/../pjsip.install --disable-video --disable-sound --enable-ext-sound --disable-speex-aec --enable-g711-codec --disable-l16-codec --disable-gsm-codec --disable-g722-codec --disable-g7221-codec --disable-speex-codec --disable-ilbc-codec --disable-sdl --disable-ffmpeg --disable-v4l2 --disable-openh264 --disable-vpx --disable-android-mediacodec --disable-darwin-ssl --disable-ssl --disable-opencore-amr --disable-silk --disable-opus --disable-bcg729 --disable-libyuv --disable-libwebrtc --disable-shared --enable-static)
	$(MAKE) -C $(PJSIP_DIR) && \
	$(MAKE) -C $(PJSIP_DIR) install

d-modem d-modem.nopulse : d-modem.c $(PKG_CONFIG_PATH)/libpjproject.pc
	$(CC) $(STATIC_FLAGS) $(if $(findstring nopulse,$@),,$(DM_CFLAGS) $(DM_LDFLAGS)) -o $@ $< \
	`PKG_CONFIG_PATH="$(PKG_CONFIG_PATH)" pkg-config --static --cflags --libs libpjproject` \
	-O2 -s

slmodemd:
	$(MAKE) -C slmodemd

clean:
	rm -f d-modem.o d-modem d-modem.nopulse
	$(MAKE) -C slmodemd clean

realclean: clean
	$(MAKE) -C $(PJSIP_DIR) realclean
	rm -rf pjsip.install/*


.PHONY: all clean realclean slmodemd
