import QtQuick
import QtQuick.Controls
import qs.Ui
import qs.Commons

// Full Miracast help surface: sticky header + scroll body + scroll cue.
Item {
  id: root

  property color foreground: Color.foreground
  property string fontFamily: Style.font.family
  property color background: Color.popups.background

  signal closeRequested()

  readonly property color muted: Qt.darker(root.foreground, 1.35)
  readonly property color faint: Qt.darker(root.foreground, 1.55)
  // Named deps so the cue updates as the user scrolls.
  readonly property real _scrollPos: helpScroll.ScrollBar.vertical.position
  readonly property real _scrollSize: helpScroll.ScrollBar.vertical.size
  readonly property bool canScrollMore: helpScroll.contentHeight > helpScroll.height
    && (_scrollPos + _scrollSize) < 0.985

  // ---- Sticky header ------------------------------------------------------
  Item {
    id: header
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: parent.top
    height: Math.max(titleText.implicitHeight, closeBtn.implicitHeight) + Style.space(4)

    Text {
      id: titleText
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      anchors.right: closeBtn.left
      anchors.rightMargin: Style.space(8)
      text: "Miracast help"
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.title
      font.bold: true
      elide: Text.ElideRight
    }

    Button {
      id: closeBtn
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      text: "Close"
      bordered: true
      foreground: root.foreground
      fontFamily: root.fontFamily
      fontSize: Style.font.caption
      horizontalPadding: Style.space(10)
      verticalPadding: Style.space(4)
      tooltipText: "Close (Esc)"
      onClicked: root.closeRequested()
    }
  }

  Rectangle {
    id: headerRule
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: header.bottom
    height: 1
    color: Util.alpha(root.foreground, 0.12)
  }

  // ---- Scrollable body ----------------------------------------------------
  ScrollView {
    id: helpScroll
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: headerRule.bottom
    anchors.topMargin: Style.space(8)
    anchors.bottom: parent.bottom
    clip: true
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    // Always show a thin bar when content overflows — clearer than AsNeeded alone.
    ScrollBar.vertical.policy: contentHeight > height ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff

    Column {
      id: body
      width: helpScroll.availableWidth
      spacing: Style.space(10)

      Text {
        width: parent.width
        text: "Cast this Omarchy desktop to a TV or dongle over Wi‑Fi Direct (not your normal Wi‑Fi SSID). Video is H.264; audio is typically LPCM on many TVs."
        color: root.muted
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        wrapMode: Text.WordWrap
      }

      // What you need
      Text {
        text: "What you need"
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        font.bold: true
      }

      Column {
        width: parent.width
        spacing: Style.space(5)
        Repeater {
          model: [
            { k: "Wi‑Fi adapter with P2P", v: "Creates the Miracast link (Intel AX201 works; a USB P2P dongle can help)" },
            { k: "FluxCast", v: "Speaks the Miracast/WFD protocol (RTSP + RTP)" },
            { k: "wf-recorder + ffmpeg", v: "Capture & encode (DMA-BUF or VAAPI pipe)" },
            { k: "dnsmasq", v: "DHCP on the P2P link" },
            { k: "NetworkManager (nmcli)", v: "Brings up Wi‑Fi Direct" },
            { k: "PipeWire / Pulse", v: "Miracast null sink for desktop audio capture" }
          ]
          delegate: Column {
            required property var modelData
            width: parent.width
            spacing: Style.space(1)
            Text {
              width: parent.width
              text: modelData.k
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              font.bold: true
              wrapMode: Text.WordWrap
            }
            Text {
              width: parent.width
              text: modelData.v
              color: root.muted
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
            }
          }
        }
      }

      Text {
        width: parent.width
        text: "Optional: UFW — if enabled, Miracast ports must be allowed (see Firewall below)."
        color: root.faint
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        wrapMode: Text.WordWrap
      }

      // CONTROLS
      Text {
        text: "Panel CONTROLS"
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        font.bold: true
      }

      Column {
        width: parent.width
        spacing: Style.space(6)
        Repeater {
          model: [
            { k: "Scan", v: "Find nearby Miracast sinks." },
            { k: "Doctor", v: "Diagnose tools, engines, encode, audio, radio, workspaces. If UFW is blocking Miracast ports, offers to open them (sudo)." },
            { k: "Info", v: "This help window." },
            { k: "Stop / Reconnect", v: "End the cast, or reconnect to the last device." }
          ]
          delegate: Column {
            required property var modelData
            width: parent.width
            spacing: Style.space(1)
            Text {
              width: parent.width
              text: modelData.k
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              font.bold: true
            }
            Text {
              width: parent.width
              text: modelData.v
              color: root.muted
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
            }
          }
        }
      }

      Text {
        width: parent.width
        text: "Persist display — keep the Extend desktop when switching TVs.\nAutomatically switch audio — after the cast is streaming, set default output to Miracast (speakers stay default during connect).\nADVANCED SETTINGS — stream mode, render engine (DMA-BUF / VAAPI / CPU), preset quality, P2P radio."
        color: root.faint
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        wrapMode: Text.WordWrap
        lineHeight: 1.25
      }

      // Firewall
      Text {
        text: "Firewall ports"
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        font.bold: true
      }

      Text {
        width: parent.width
        text: "Needed when UFW (or another firewall) is active:"
        color: root.muted
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        wrapMode: Text.WordWrap
      }

      Column {
        width: parent.width
        spacing: Style.space(3)
        Repeater {
          model: [
            { k: "7236/tcp", v: "WFD RTSP control" },
            { k: "67/udp, 68/udp", v: "DHCP on P2P" },
            { k: "19000–19100/udp", v: "Local RTP" },
            { k: "42000–42100/udp", v: "Sink RTP/RTCP" }
          ]
          delegate: Row {
            required property var modelData
            width: parent.width
            spacing: Style.space(8)
            Text {
              width: Style.space(128)
              text: modelData.k
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              font.bold: true
              wrapMode: Text.WordWrap
            }
            Text {
              width: parent.width - Style.space(136)
              text: modelData.v
              color: root.muted
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
            }
          }
        }
      }

      Text {
        width: parent.width
        text: "If ufw is not installed, Doctor cannot open ports for you — allow the list above in nftables/firewalld/your router policy as needed."
        color: root.faint
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        wrapMode: Text.WordWrap
      }

      // Tips
      Text {
        text: "Tips for a good picture"
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        font.bold: true
      }

      Column {
        width: parent.width
        spacing: Style.space(3)
        Repeater {
          model: [
            "20 MHz P2P budget ≈ 12–14 Mbps video. Best (Dynamic) starts Medium and adapts from retries/throughput; avoid pipe quality=1 (can hang).",
            "2.4-only sinks (e.g. Realtek 8192CU) force MCC when laptop Wi‑Fi is on 5 GHz — occasional brief glitches are expected; a dual-band sink unlocks 5 GHz SCC.",
            "“Quiet” channel pick ranks AP interference scores (lower better); that ranking matched TX retries on-device.",
            "After changing engine or preset quality, reconnect (or restart-capture) so encode settings reload.",
            "Keep eDP and Miracast desktops separate (ext-* on the TV, numbers on the laptop)."
          ]
          delegate: Text {
            required property string modelData
            width: parent.width
            text: "•  " + modelData
            color: root.muted
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }
        }
      }

      // Troubleshooting
      Text {
        text: "Troubleshooting"
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        font.bold: true
      }

      Column {
        width: parent.width
        spacing: Style.space(3)
        Repeater {
          model: [
            "Run Doctor and read STATUS (warnings name the area: audio, firewall, link, radio_channel, …).",
            "Confirm the TV is in Miracast / screen-mirroring receive mode.",
            "If video is fine but silent: set Sound default to Miracast (or enable auto-switch) and raise that sink’s volume.",
            "If Doctor says live encode ≠ settings: reconnect.",
            "Frozen picture with audio still going: wait for auto restart-capture, or run miracast-ctl restart-capture.",
            "Brief glitches with TX still healthy: usually 2.4 GHz RF / MCC — quieter channel, slightly lower bitrate, or a 5 GHz-capable sink.",
            "Logs: ~/.local/state/omarchy-miracast/logs/cast.log / link-watch.log"
          ]
          delegate: Text {
            required property string modelData
            required property int index
            width: parent.width
            text: (index + 1) + ".  " + modelData
            color: root.muted
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }
        }
      }

      Text {
        width: parent.width
        text: "CLI: miracast-ctl doctor --fix · info · scan"
        color: root.faint
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        wrapMode: Text.WordWrap
      }

      Item { width: 1; height: Style.space(8) }
    }
  }

  // ---- Scroll affordance --------------------------------------------------
  // Bottom fade + label when more content exists below the fold.
  Item {
    id: scrollCue
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.bottom: parent.bottom
    height: Style.space(36)
    visible: root.canScrollMore
    opacity: visible ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: 120 } }

    Rectangle {
      anchors.fill: parent
      gradient: Gradient {
        GradientStop { position: 0.0; color: "transparent" }
        GradientStop { position: 0.35; color: Util.alpha(root.background, 0.55) }
        GradientStop { position: 1.0; color: Util.alpha(root.background, 0.97) }
      }
    }

    Text {
      anchors.horizontalCenter: parent.horizontalCenter
      anchors.bottom: parent.bottom
      anchors.bottomMargin: Style.space(4)
      text: "Scroll for more  ↓"
      color: root.muted
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
    }
  }
}
