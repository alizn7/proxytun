import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import GObject from 'gi://GObject';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import {QuickMenuToggle, SystemIndicator} from 'resource:///org/gnome/shell/ui/quickSettings.js';

const STATUS_FILE = '/run/proxytun/status.json';
const PROFILES_FILE = '/etc/proxytun/profiles.json';
const ICON = 'network-vpn-symbolic';
const POLL_SECONDS = 3;

const STATE_LABELS = {
    starting: 'Connecting…',
    active: 'Connected',
    fallback: 'Proxy down · direct',
    blocked: 'Proxy down · blocked',
    error: 'Error · see proxytun logs',
};

function readJson(path) {
    try {
        const [, bytes] = GLib.file_get_contents(path);
        return JSON.parse(new TextDecoder().decode(bytes));
    } catch {
        return null;
    }
}

// Unit start/stop is authorized by the polkit rule installed with proxytun.
function systemctl(args, done) {
    try {
        const proc = Gio.Subprocess.new(['systemctl', ...args], Gio.SubprocessFlags.STDERR_SILENCE);
        proc.wait_async(null, (p, res) => {
            try {
                p.wait_finish(res);
            } catch (e) {
                console.error(`proxytun: ${e.message}`);
            }
            done?.();
        });
    } catch (e) {
        console.error(`proxytun: ${e.message}`);
        done?.();
    }
}

const unitOf = name => `proxytun@${name}.service`;

const ProxyToggle = GObject.registerClass(
class ProxyToggle extends QuickMenuToggle {
    _init() {
        super._init({title: 'Proxy', iconName: ICON, toggleMode: false});
        this.menu.setHeader(ICON, 'proxytun', 'Route all traffic through a proxy');
        this._section = new PopupMenu.PopupMenuSection();
        this.menu.addMenuItem(this._section);
        this._menuKey = null;
        this._status = null;
        this._store = null;

        this.connect('clicked', () => this._onClicked());
        this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, POLL_SECONDS, () => {
            this._sync();
            return GLib.SOURCE_CONTINUE;
        });
        this.connect('destroy', () => {
            if (this._timer) {
                GLib.Source.remove(this._timer);
                this._timer = 0;
            }
        });
        this._sync();
    }

    _sync() {
        this._status = readJson(STATUS_FILE);
        this._store = readJson(PROFILES_FILE);
        const st = this._status;
        this.checked = Boolean(st);
        if (st)
            this.subtitle = `${st.profile} · ${STATE_LABELS[st.state] ?? st.state}`;
        else
            this.subtitle = this._store?.last ?? 'Off';
        this._syncMenu();
    }

    _syncMenu() {
        const profiles = this._store?.profiles ?? {};
        const names = Object.keys(profiles).sort();
        const active = this._status?.profile ?? null;
        const key = JSON.stringify([names.map(n => [n, profiles[n].host, profiles[n].port]), active]);
        if (key === this._menuKey)
            return;
        this._menuKey = key;
        this._section.removeAll();

        if (names.length === 0) {
            const hint = this._store ? 'No profiles yet: proxytun on HOST' : 'Profiles unreadable: log out and back in';
            this._section.addMenuItem(new PopupMenu.PopupMenuItem(hint, {reactive: false}));
            return;
        }
        for (const name of names) {
            const p = profiles[name];
            const item = new PopupMenu.PopupMenuItem(`${name}   ${p.host}:${p.port}`);
            item.setOrnament(name === active ? PopupMenu.Ornament.CHECK : PopupMenu.Ornament.NONE);
            item.connect('activate', () => this._start(name));
            this._section.addMenuItem(item);
        }
    }

    _onClicked() {
        if (this._status)
            systemctl(['stop', unitOf(this._status.profile)], () => this._sync());
        else if (this._store?.last)
            this._start(this._store.last);
        else
            this.menu.open();
    }

    _start(name) {
        const running = this._status?.profile;
        if (running === name)
            return;
        const start = () => systemctl(['start', unitOf(name)], () => this._sync());
        if (running)
            systemctl(['stop', unitOf(running)], start);
        else
            start();
    }
});

const ProxyIndicator = GObject.registerClass(
class ProxyIndicator extends SystemIndicator {
    _init() {
        super._init();
        this._indicator = this._addIndicator();
        this._indicator.iconName = ICON;
        const toggle = new ProxyToggle();
        toggle.bind_property('checked', this._indicator, 'visible', GObject.BindingFlags.SYNC_CREATE);
        this.quickSettingsItems.push(toggle);
    }
});

export default class ProxytunExtension extends Extension {
    enable() {
        this._indicator = new ProxyIndicator();
        Main.panel.statusArea.quickSettings.addExternalIndicator(this._indicator);
    }

    disable() {
        this._indicator.quickSettingsItems.forEach(item => item.destroy());
        this._indicator.destroy();
        this._indicator = null;
    }
}
