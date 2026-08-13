# Put "Excephalon" in the Start Menu: a shortcut to pythonw launch.pyw with the repo's icon.
# Run from anywhere; everything is derived from this script's own location. Re-run after moving
# the repo. Run it once after cloning; it creates the entry, nothing else is needed.
#
# The shortcut names a FILE, never `-m excephalon`: a .lnk keeps whatever it was installed with,
# and when the module was renamed every shortcut on the machine went on asking for the old name
# and clicking did nothing at all, for a week. Nothing here can reach into an installed shortcut
# to correct that - a path into the checkout is what stays true across a rename.
$repo = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $repo ".venv\Scripts\pythonw.exe"
$launcher = Join-Path $repo "launch.pyw"
$icon = Join-Path $repo "assets\excephalon.ico"

$menu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Excephalon.lnk"
# A pin is a COPY of the shortcut, not a link to it, so it keeps whatever it was pinned with -
# which is how a corrected Start Menu entry would sit beside a taskbar button still asking for
# the old name, still doing nothing on a click. The pin is the one he actually presses.
$pin = Join-Path $env:APPDATA `
    "Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\Excephalon.lnk"

$shell = New-Object -ComObject WScript.Shell
foreach ($shortcut in @($menu, $pin)) {
    # The menu entry is created if it isn't there; the pin is only ever UPDATED, because writing
    # a .lnk into that folder pins nothing - it just leaves a file nobody will ever see.
    if ($shortcut -eq $pin -and -not (Test-Path $pin)) {
        Write-Output "no taskbar pin to update"
        continue
    }
    $link = $shell.CreateShortcut($shortcut)
    $link.TargetPath = $pythonw
    $link.Arguments = '"' + $launcher + '"'
    $link.WorkingDirectory = $repo
    $link.IconLocation = $icon
    $link.Description = "Excephalon - voice companion"
    $link.Save()
    Write-Output "installed $shortcut"
}

# Stamp the shortcuts with the same AppUserModelID the app declares, so pinning one and running it
# are the SAME taskbar button - without this a pin sits inert while the running window lights up
# somewhere else. Recreating the shortcut drops the id, which is why this runs right here. No
# argument, so it does the pinned copy too: a pin is a COPY and keeps the id it was made with.
& (Join-Path $repo ".venv\Scripts\python.exe") (Join-Path $PSScriptRoot "stamp-shortcut-appid.py")
