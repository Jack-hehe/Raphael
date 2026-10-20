; NSIS installer hooks (bundle > windows > nsis > installerHooks in tauri.release.conf.json).
;
; The core keeps raphael.db, device.json and the downloaded speech models (about 1.2 GB) in
; %APPDATA%\Raphael. The template's "Delete the application data" option removes only
; $APPDATA\${BUNDLEID} and $LOCALAPPDATA\${BUNDLEID}, so this hook removes the core's folder as well.
;
; It uses the template's own two conditions (installer.nsi, Section Uninstall, of tauri-bundler
; 2.10.3, which is built into @tauri-apps/cli 2.12.3 in app/package-lock.json; check again when the
; CLI changes): the box on the uninstall confirmation page is ticked, and this is not an update. A
; silent or passive (/P) uninstall never shows that page, and an update runs the old uninstaller
; with /UPDATE, so all three keep the data.
; The OpenRouter key is a Windows Credential Manager entry, not a file, and is left alone.
!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode <> 1
    SetShellVarContext current
    RmDir /r "$APPDATA\Raphael"
  ${EndIf}
!macroend
