fn main() {
    let private_icon = "icons-private/icon.ico";
    let public_icon = "icons/icon.ico";
    println!("cargo:rerun-if-changed={private_icon}");
    println!("cargo:rerun-if-changed={public_icon}");

    let icon = if std::path::Path::new(private_icon).is_file() {
        private_icon
    } else {
        public_icon
    };
    let attributes = tauri_build::Attributes::new().windows_attributes(
        tauri_build::WindowsAttributes::new().window_icon_path(icon),
    );
    tauri_build::try_build(attributes).expect("failed to run tauri-build");
}
