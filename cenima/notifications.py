"""Optional native completion notifications with no required desktop dependency."""

import os
import shutil
import subprocess


def notify(title, message):
    if os.name == 'nt' and shutil.which('powershell'):
        script = ("$n=New-Object -ComObject WScript.Shell; "
                  "$n.Popup([string]%s,5,[string]%s,64)" % (
                      repr(message.replace('"', "'")), repr(title.replace('"', "'"))))
        subprocess.Popen(['powershell', '-NoProfile', '-Command', script],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    command = shutil.which('notify-send')
    if command:
        subprocess.Popen([command, title, message], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    return False
