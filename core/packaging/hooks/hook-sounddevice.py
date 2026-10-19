"""Ship the Windows x64 non-ASIO PortAudio library used by Raphael."""

from pathlib import Path

from PyInstaller.utils.hooks import get_module_file_attribute


package = Path(get_module_file_attribute("_sounddevice_data")).parent
library = package / "portaudio-binaries" / "libportaudio64bit.dll"
if not library.is_file():
    raise RuntimeError(f"The Windows x64 PortAudio library is missing: {library}")
binaries = [(str(library), "_sounddevice_data/portaudio-binaries")]
datas = [(str(package / "portaudio-binaries" / "README.md"),
          "_sounddevice_data/portaudio-binaries")]
