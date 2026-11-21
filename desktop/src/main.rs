use std::process::Command;
use tauri::menu::{Menu, MenuItem};
use tauri::{Manager, webview::NewWindowResponse, Url, WebviewUrl, WebviewWindowBuilder};

fn open_external(url: &Url) {
    if matches!(url.scheme(), "https" | "http" | "tg" | "mailto") {
        if let Err(error) = Command::new("/usr/bin/open").arg(url.as_str()).status() {
            eprintln!("Could not open the link: {error}");
        }
    }
}

fn main() {
    tauri::Builder::default()
        .menu(|app| {
            let menu = Menu::default(app)?;
            let refresh = MenuItem::with_id(app, "refresh-screen", "Обновить экран", true, Some("Cmd+R"))?;
            for item in menu.items()? {
                if let Some(view) = item.as_submenu() {
                    if view.text()? == "View" {
                        view.insert(&refresh, 0)?;
                        break;
                    }
                }
            }
            Ok(menu)
        })
        .on_menu_event(|app, event| {
            if event.id().as_ref() == "refresh-screen" {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.eval("window.dispatchEvent(new Event('todogram-refresh'))");
                }
            }
        })
        .setup(|app| {
            let site = Url::parse("https://tasks.isite.pro/")?;
            let origin = site.origin();
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(site))
                .title("Todogram")
                .inner_size(1200.0, 800.0)
                .min_inner_size(375.0, 480.0)
                .on_navigation(move |url| {
                    if url.origin() == origin {
                        return true;
                    }
                    open_external(url);
                    false
                })
                .on_new_window(|url, _| {
                    open_external(&url);
                    NewWindowResponse::Deny
                })
                .build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("Could not start Todogram");
}
