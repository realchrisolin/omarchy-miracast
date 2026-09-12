import QtQuick
import qs.Commons

// Display glyph + Miracast wifi mark centered in the monitor screen.
Item {
  id: root

  property real iconSize: Style.bar.iconFont
  property color color: Color.foreground
  property string phase: "idle"
  property bool multiDisplay: false
  property string fontFamily: Style.font.family

  readonly property bool connecting: phase === "connecting" || phase === "dhcp" || phase === "rtsp" || phase === "scanning"
  readonly property bool streaming: phase === "streaming"
  readonly property bool showSignal: connecting || streaming

  width: iconSize
  height: iconSize
  implicitWidth: iconSize
  implicitHeight: iconSize

  Text {
    id: displayGlyph
    anchors.centerIn: parent
    // Always the single-display glyph. Miracast Extend adds a virtual
    // output (peer-named or HEADLESS-*) that would otherwise flip this
    // to the dual-monitor icon.
    text: "󰍹"
    color: root.color
    font.family: root.fontFamily
    font.pixelSize: root.iconSize
    renderType: Text.NativeRendering
  }

  // Nested in the monitor glyph's screen; keep smaller than the display mark
  // and bias left so it reads inside the panel rather than on the bezel.
  Text {
    id: wifiMark
    visible: root.showSignal
    text: "󰖩"
    color: root.color
    font.family: root.fontFamily
    // Bar icons are ~13px. Original ~8px was large; 4px was too small — land
    // near 6px so it reads as a mark inside the display glyph.
    font.pixelSize: Math.max(6, Math.round(root.iconSize * 0.42))
    renderType: Text.NativeRendering
    anchors.horizontalCenter: displayGlyph.horizontalCenter
    anchors.verticalCenter: displayGlyph.verticalCenter
    // ~0.7px right of center; ~1.5px up (2px up, then 0.5px back down).
    anchors.horizontalCenterOffset: root.iconSize * 0.056
    anchors.verticalCenterOffset: -(Math.max(1, Math.round(root.iconSize * 0.08)) + 1) + 0.5
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
}
