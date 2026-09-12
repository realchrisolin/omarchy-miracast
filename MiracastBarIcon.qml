import QtQuick
import qs.Commons

// Display glyph + Miracast wifi overlay.
//
// NativeRendering + font hinting make painted ink non-linear with pixelSize, so
// iconSize fractions only look right at one size (often ~2× / display). Use
// QtRendering + PreferNoHinting so both glyphs scale linearly, then center
// wifi on the parent with a single optical nudge (monitor chin) expressed as
// a fraction of renderedSize — stable across bar/hero and global text size.
Item {
  id: root

  property real iconSize: Style.bar.iconFont
  property color color: Color.foreground
  property string phase: "idle"
  property bool multiDisplay: false
  property string fontFamily: Style.font.family
  property bool debugBounds: false
  // Extra optical nudge for the wifi overlay (pixels). Hero size often needs
  // a small positive value so the arcs sit lower in the monitor glass.
  property real wifiVerticalNudge: 0

  readonly property bool connecting: phase === "connecting" || phase === "dhcp" || phase === "rtsp" || phase === "scanning"
  readonly property bool streaming: phase === "streaming"
  readonly property bool showSignal: connecting || streaming

  readonly property int renderedSize: Math.max(1, Math.round(iconSize))

  // Optical center of 󰍹's screen glass relative to the glyph em-box center.
  // Chin/stand sits below the glass; larger wifi arcs are bottom-heavy so lift
  // a bit more. +0.025 unit right/up from the previous center.
  readonly property real glassOffsetX: renderedSize * 0.025
  readonly property real glassOffsetY: -(renderedSize * 0.115) + wifiVerticalNudge

  // Wifi fills most of the glass; keep a little margin for the bezel.
  readonly property int wifiSize: Math.max(1, Math.round(renderedSize * 0.54))

  width: iconSize
  height: iconSize
  implicitWidth: iconSize
  implicitHeight: iconSize

  Text {
    id: displayGlyph
    anchors.centerIn: parent
    textFormat: Text.PlainText
    text: "󰍹"
    color: root.color
    font.family: root.fontFamily
    font.pixelSize: root.renderedSize
    font.hintingPreference: Font.PreferNoHinting
    // Distance-field path — scales linearly (unlike NativeRendering hinting).
    renderType: Text.QtRendering
  }

  Text {
    id: wifiMark
    visible: root.showSignal
    anchors.centerIn: parent
    anchors.horizontalCenterOffset: root.glassOffsetX
    anchors.verticalCenterOffset: root.glassOffsetY
    textFormat: Text.PlainText
    text: "󰖩"
    color: root.color
    font.family: root.fontFamily
    font.pixelSize: root.wifiSize
    font.hintingPreference: Font.PreferNoHinting
    renderType: Text.QtRendering
    opacity: root.connecting ? pulse.opacity : 1.0
    z: 2

    SequentialAnimation {
      id: pulse
      property real opacity: 1.0
      loops: Animation.Infinite
      running: root.connecting
      NumberAnimation {
        target: pulse
        property: "opacity"
        from: 0.35
        to: 1.0
        duration: 480
        easing.type: Easing.InOutQuad
      }
      NumberAnimation {
        target: pulse
        property: "opacity"
        from: 1.0
        to: 0.35
        duration: 480
        easing.type: Easing.InOutQuad
      }
    }
  }

  Rectangle {
    visible: root.debugBounds
    anchors.fill: parent
    color: "transparent"
    border.width: 1
    border.color: "#4488ff"
  }

  Rectangle {
    visible: root.debugBounds
    width: 4
    height: 4
    radius: 2
    color: "#ff4488"
    x: root.width / 2 + root.glassOffsetX - width / 2
    y: root.height / 2 + root.glassOffsetY - height / 2
  }
}
