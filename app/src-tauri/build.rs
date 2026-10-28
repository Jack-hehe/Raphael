fn main() {
    // Tests that start the app on Tauri's mock runtime link the menu code, which imports
    // `TaskDialogIndirect` from version 6 of the common controls. Windows finds that version only
    // through a manifest, and Tauri gives one to the app alone: without it a test executable
    // cannot even start.
    if std::env::var("CARGO_CFG_TARGET_ENV").as_deref() == Ok("msvc") {
        let manifest = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests")
            .join("common-controls.manifest");
        println!("cargo:rerun-if-changed=tests/common-controls.manifest");
        println!("cargo:rustc-link-arg-tests=/MANIFEST:EMBED");
        println!(
            "cargo:rustc-link-arg-tests=/MANIFESTINPUT:{}",
            manifest.display()
        );
    }
    tauri_build::build();
}
