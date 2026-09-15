import QtQuick
import QtQuick.Controls
import qs.Ui
import qs.Commons

// Themed Miracast help body for the floating KeyboardPanel card.
// Parent owns open/close; this emits closeRequested for the Close button.
Item {
  id: root

  property color foreground: Color.foreground
  property string fontFamily: Style.font.family

  signal closeRequested()

  readonly property color muted: Qt.darker(root.foreground, 1.35)
  readonly property color faint: Qt.darker(root.foreground, 1.55)

  implicitWidth: column.implicitWidth
  implicitHeight: column.implicitHeight

  function sectionTitle(text) {
    return text
  }

  Column {
    id: column
    width: parent.width
    spacing: Style.space(10)

    // ---- Header -----------------------------------------------------------
    Item {
      width: parent.width
      height: Math.max(titleText.implicitHeight, closeBtn.implicitHeight)

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

    Text {
      width: parent.width
      text: "Cast to a TV or dongle over Wi‑Fi Direct (not your home Wi‑Fi)."
      color: root.muted
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      wrapMode: Text.WordWrap
    }

    Rectangle {
      width: parent.width
      height: 1
      color: Util.alpha(root.foreground, 0.12)
    }

    // ---- What you need ----------------------------------------------------
    Text {
      text: "What you need"
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
          "Wi‑Fi adapter with P2P (Intel AX201 OK; USB P2P dongle helps)",
          "FluxCast — Miracast / WFD protocol",
          "wf-recorder + ffmpeg — capture & encode",
          "dnsmasq — DHCP on the P2P link",
          "NetworkManager (nmcli) — Wi‑Fi Direct",
          "PipeWire — Miracast audio sink"
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

    Text {
      width: parent.width
      text: "Optional: UFW — if active, Miracast ports must be allowed."
      color: root.faint
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      wrapMode: Text.WordWrap
    }

    // ---- CONTROLS ---------------------------------------------------------
    Text {
      text: "CONTROLS"
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.body
      font.bold: true
    }

    Column {
      width: parent.width
      spacing: Style.space(4)
      Repeater {
        model: [
          { k: "Scan", v: "Find nearby sinks" },
          { k: "Check & fix", v: "Diagnose; open UFW ports if blocked" },
          { k: "Info", v: "This help" },
          { k: "Stop / Reconnect", v: "End cast, or reconnect last device" }
        ]
        delegate: Row {
          required property var modelData
          width: parent.width
          spacing: Style.space(8)
          Text {
            width: Style.space(118)
            text: modelData.k
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            font.bold: true
            wrapMode: Text.WordWrap
          }
          Text {
            width: parent.width - Style.space(126)
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
      text: "Also: Persist display · Automatically switch audio (after streaming starts) · ADVANCED SETTINGS (stream / engine / quality / radio)."
      color: root.faint
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      wrapMode: Text.WordWrap
    }

    // ---- Firewall ---------------------------------------------------------
    Text {
      text: "Firewall ports"
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
          { k: "7236/tcp", v: "RTSP control" },
          { k: "67–68/udp", v: "DHCP" },
          { k: "19000–19100/udp", v: "Local RTP" },
          { k: "42000–42100/udp", v: "Sink RTP/RTCP" }
        ]
        delegate: Row {
          required property var modelData
          width: parent.width
          spacing: Style.space(8)
          Text {
            width: Style.space(130)
            text: modelData.k
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            font.bold: true
          }
          Text {
            text: modelData.v
            color: root.muted
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
          }
        }
      }
    }

    Text {
      width: parent.width
      text: "No ufw? Open these in nftables/firewalld yourself — Check & fix cannot."
      color: root.faint
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      wrapMode: Text.WordWrap
    }

    // ---- Tips -------------------------------------------------------------
    Text {
      text: "Picture tips"
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
          "Prefer DMA-BUF + preset High when stable",
          "On a 20 MHz P2P link, pipe path uses capped QVBR",
          "After engine/quality changes: Reconnect",
          "Keep laptop (eDP) and TV (ext-*) workspaces separate"
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

    // ---- Stuck ------------------------------------------------------------
    Text {
      text: "Stuck?"
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
          "Check & fix — read STATUS warnings (audio, firewall, link, …)",
          "Put the TV in Miracast / screen-mirror receive mode",
          "Silent but video OK → Sound default Miracast (or auto-switch)",
          "Live encode ≠ settings → reconnect",
          "Logs: ~/.local/state/omarchy-miracast/logs/cast.log"
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
  }
}
