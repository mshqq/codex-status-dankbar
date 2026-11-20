import QtQuick
import qs.Modules.Plugins
import qs.Widgets

PluginSettings {
    pluginId: "codexStatus"

    StringSetting {
        settingKey: "codexPath"
        label: "Codex CLI path"
        description: "Absolute path to Codex or your proxy wrapper."
        placeholder: "/usr/bin/codex"
        defaultValue: ""
    }

    StringSetting {
        settingKey: "proxyUrl"
        label: "Proxy URL"
        description: "HTTP(S) or SOCKS5 proxy for Codex. Use a URL without credentials."
        placeholder: "http://127.0.0.1:7890"
        defaultValue: ""
    }

    ToggleSetting {
        settingKey: "enableApprovals"
        label: "Approvals in DankBar"
        description: "Review requests from the Codex background server."
        defaultValue: false
    }

    StringSetting {
        settingKey: "approvalSocket"
        label: "App Server socket"
        description: "Optional absolute path to a custom socket."
        placeholder: "Use the default Codex socket"
        defaultValue: ""
    }
}
