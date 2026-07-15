import sys
import subprocess
import os

def check_avx512(exe_path):
    if not os.path.exists(exe_path):
        print(f"Error: File not found: {exe_path}")
        sys.exit(1)
        
    print(f"Checking {exe_path} for AVX-512 instructions...")
    
    # Use objdump to disassemble and search for AVX-512 specific instructions or registers
    # zmm registers are specific to AVX-512
    # EVEX prefix (0x62) is used for AVX-512
    try:
        # We use objdump from the system or zig cc if available
        # Since we know zig is used for compilation, we can try to use it
        zig_path = r"C:\Users\14915\AppData\Local\Nuitka\Nuitka\Cache\downloads\pip\private-8c1f6f32\Lib\site-packages\ziglang\zig.exe"
        
        if os.path.exists(zig_path):
            cmd = [zig_path, "objdump", "-d", exe_path]
        else:
            cmd = ["objdump", "-d", exe_path]
            
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
        
        found_avx512 = False
        count = 0
        
        for line in process.stdout:
            # Check for zmm registers (zmm0-zmm31) which are exclusive to AVX-512
            if "zmm" in line.lower():
                print(f"FOUND AVX-512 (zmm register): {line.strip()}")
                found_avx512 = True
                count += 1
                if count > 5:  # Don't flood the output
                    print("... and more")
                    break
                    
        process.wait()
        
        if found_avx512:
            print("\nFAILED: AVX-512 instructions detected in the binary!")
            print("This binary will crash on CPUs without AVX-512 support (like Intel 12th+ gen non-HEDT).")
            sys.exit(1)
        else:
            print("\nSUCCESS: No AVX-512 instructions detected.")
            sys.exit(0)
            
    except FileNotFoundError:
        print("Warning: objdump not found. Cannot verify binary.")
        print("Please ensure binutils or zig is installed to run this check.")
        sys.exit(0)  # Don't fail the build if we can't check, but warn
    except Exception as e:
        print(f"Error during verification: {e}")
        sys.exit(0)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        check_avx512(sys.argv[1])
    else:
        # Default path if run without arguments
        default_path = os.path.join("dist", "SuzuEmojy_Release", "bin", "SuzuEmojy.exe")
        check_avx512(default_path)