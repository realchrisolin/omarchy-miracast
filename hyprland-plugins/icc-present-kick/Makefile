# Build: make
# Load:  hyprctl plugin load $PWD/icc-present-kick.so
# (miracast-ctl loads this automatically when using ICC)

CXX ?= g++
PLUGIN = icc-present-kick.so

CXXFLAGS += -shared -fPIC --no-gnu-unique -std=c++23 \
	$(shell pkg-config --cflags hyprland aquamarine libdrm libinput pangocairo pixman-1) \
	-I/usr/include/hyprland/protocols

LDFLAGS += -shared

all: $(PLUGIN)

$(PLUGIN): main.cpp
	$(CXX) $(CXXFLAGS) -o $@ $< $(LDFLAGS)

clean:
	rm -f $(PLUGIN)

.PHONY: all clean
