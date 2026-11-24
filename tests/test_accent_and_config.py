import cenima.cli as cli_mod
from cenima.accent import adjust_brightness, hex_to_rgb, rgb_to_hex


def test_color_utilities():
    assert hex_to_rgb("#fff") == (255, 255, 255)
    assert hex_to_rgb("#1a2b3c") == (26, 43, 60)
    assert rgb_to_hex((26, 43, 60)) == "#1a2b3c"
    brighter = adjust_brightness("#336699", 1.2)
    assert brighter.startswith("#") and len(brighter) == 7


def test_config_static_theme_is_lazy_and_roundtrips(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "cfg"
    cfg_file = cfg_dir / "config.json"
    monkeypatch.setattr(cli_mod, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(cli_mod, "CONFIG_FILE", cfg_file)

    called = []

    def fail_if_called(*args, **kwargs):
        called.append(True)
        return {"primary": "#000000", "secondary": "#111111", "accent": "#222222", "error": "#d97979", "source": "Mock"}

    monkeypatch.setattr(cli_mod, "get_auto_theme", fail_if_called)

    cfg = cli_mod.Config()
    cfg.theme = "blue"
    cfg.mpv_args = ["--fs", "--hwdec=auto"]
    cfg.save()

    theme = cfg.get_theme()
    assert theme["primary"] == "#7eb3d4"
    assert called == []

    reloaded = cli_mod.Config()
    assert reloaded.theme == "blue"
    assert reloaded.mpv_args == ["--fs", "--hwdec=auto"]

    reloaded.theme = "auto"
    reloaded.get_theme()
    assert len(called) == 1
