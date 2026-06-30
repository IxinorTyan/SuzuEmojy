import os
import subprocess
import sys

def main():
    print("====================================")
    print("Building SuzuEmojy with Nuitka")
    print("====================================")

    # Nuitka command
    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--windows-disable-console",
        "--enable-plugin=pyside6",
        "--windows-icon-from-ico=hi.ico",
        "--include-data-file=hi.ico=hi.ico",
        "--output-dir=dist",
        "--assume-yes-for-downloads",
        "--include-package=qfluentwidgets",
        "--include-package=services",
        "--include-package=fluent_ui",
        "main.py"
    ]

    print("Running command:", " ".join(cmd))
    print("This may take a while, please wait...")
    
    # Run the build process
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    
    for line in process.stdout:
        print(line, end="")
        
    process.wait()
    
    if process.returncode == 0:
        print("\n====================================")
        print("Build Complete!")
        print("You can find the executable in the 'dist/main.dist' folder.")
        print("====================================")
    else:
        print("\n====================================")
        print("Build failed with return code", process.returncode)
        print("====================================")

if __name__ == "__main__":
    main()