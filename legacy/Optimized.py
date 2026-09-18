import subprocess
import sys
import time

def run_cmd(cmd):
    """Utility to run shell commands and return output."""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        return e.stderr.strip()

def check_devices():
    print("[*] Searching for connected ADB devices...")
    output = run_cmd("adb devices")
    lines = output.splitlines()
    devices = [line.split("\t")[0] for line in lines[1:] if "\tdevice" in line]
    return devices

def fetch_device_info():
    print("\n" + "="*50)
    print(" DEVICE IDENTIFICATION & CONFIRMATION")
    print("="*50)
    
    brand = run_cmd("adb shell getprop ro.product.brand")
    model = run_cmd("adb shell getprop ro.product.model")
    android_version = run_cmd("adb shell getprop ro.build.version.release")
    device_name = run_cmd("adb shell getprop ro.product.device")
    
    print(f" -> Brand          : {brand.capitalize()}")
    print(f" -> Model Name     : {model}")
    print(f" -> Device Code    : {device_name}")
    print(f" -> Android Version: {android_version}")
    
    confirm = input("\nDoes this match your connected phone? (y/n): ").strip().lower()
    if confirm != 'y':
        print("[!] Device confirmation failed. Aborting script.")
        sys.exit(0)
    print("[+] Device verified successfully!")

def inspect_unneeded_packages():
    print("\n" + "="*50)
    print(" SCANNING UNNEEDED / BLOAT PACKAGES")
    print("="*50)
    print("Fetching third-party and pre-installed packages from user profile...")
    
    installed_raw = run_cmd("adb shell pm list packages -3") # -3 lists third party/installed app packages
    all_packages = [p.replace("package:", "").strip() for p in installed_raw.splitlines() if p.strip()]
    
    # Common safe-to-remove or bloat identifiers categories
    known_bloat_keywords = ["facebook", "netflix", "tiktok", "instagram", "bloat", "carrier", "partner", "games", "analytics", "bixby", "duo"]
    
    flagged_bloat = []
    other_third_party = []
    
    for pkg in all_packages:
        if any(keyword in pkg.lower() for keyword in known_bloat_keywords):
            flagged_bloat.append(pkg)
        else:
            other_third_party.append(pkg)
            
    print(f"\n[+] Total Third-Party/User Apps Found: {len(all_packages)}")
    print(f"[!] Flagged Potential Bloat/Telemetry Packages ({len(flagged_bloat)}):")
    for pkg in flagged_bloat:
        print(f"    - {pkg}")
        
    print(f"\n[i] Other User-Installed Apps ({len(other_third_party)}):")
    for pkg in other_third_party[:10]: # Print up to first 10 to avoid wall of text
        print(f"    - {pkg}")
    if len(other_third_party) > 10:
        print(f"    ... and {len(other_third_party) - 10} more.")
        
    return flagged_bloat

def run_fps_check():
    print("\n" + "="*50)
    print(" DIAGNOSTIC: FPS & PERFORMANCE CHECKER")
    print("="*50)
    print("Enabling GPU rendering profile visual bars on your device screen...")
    run_cmd("adb shell setprop debug.hwui.profile visual_bars")
    print("-> Bars enabled! Check your phone screen for rendering spikes.")
    
    print("\nStarting live terminal frame stats stream (Press Ctrl+C to skip)...")
    print("-" * 50)
    try:
        process = subprocess.Popen("adb shell dumpsys SurfaceFlinger --latency", shell=True, stdout=subprocess.PIPE, text=True)
        for _ in range(12):
            line = process.stdout.readline()
            if not line:
                break
            print(f"[FPS Stat]: {line.strip()}")
            time.sleep(0.4)
        process.terminate()
    except KeyboardInterrupt:
        print("\n[!] Bypassed live stream collection.")
        
    run_cmd("adb shell setprop debug.hwui.profile false")
    print("[+] Diagnostic overlay disabled.")

def optimize_light():
    print("\n--- Executing [LIGHT] Optimization ---")
    print("[-] Adjusting animation scaling factors to 0.5x...")
    run_cmd("adb shell settings put global window_animation_scale 0.5")
    run_cmd("adb shell settings put global transition_animation_scale 0.5")
    run_cmd("adb shell settings put global animator_duration_scale 0.5")
    print("[-] Trimming system package caches...")
    run_cmd("adb shell pm trim-caches 999G")
    print("[+] Light optimization complete!")

def optimize_heavy(flagged_bloat):
    print("\n--- Executing [HEAVY] Optimization ---")
    optimize_light()
    
    print("[-] Forcing immediate Doze mode profile...")
    run_cmd("adb shell dumpsys deviceidle force-idle")
    
    print("[-] Disabling identified bloatware packages...")
    for pkg in flagged_bloat:
        res = run_cmd(f"adb shell pm disable-user --user 0 {pkg}")
        print(f"    -> Disabled {pkg}: {res}")
    print("[+] Heavy optimization complete!")

def optimize_aggressive(flagged_bloat):
    print("\n--- Executing [AGGRESSIVE] Optimization ---")
    optimize_heavy(flagged_bloat)
    
    print("[-] Stripping UI animations completely (0.0x)...")
    run_cmd("adb shell settings put global window_animation_scale 0.0")
    run_cmd("adb shell settings put global transition_animation_scale 0.0")
    run_cmd("adb shell settings put global animator_duration_scale 0.0")
    
    print("[-] Clearing system logcat logs to drop background I/O overhead...")
    run_cmd("adb shell logcat -c")
    print("[+] Aggressive optimization complete!")

def main():
    print("=== Advanced Android ADB Optimizer & Inspector ===")
    devices = check_devices()
    
    if not devices:
        print("[-] No ADB devices detected. Check connection and USB Debugging settings.")
        sys.exit(1)
        
    print(f"[+] Active ADB bridge found for device IDs: {devices}")
    
    # Step 1: Pull and confirm phone model over terminal
    fetch_device_info()
    
    # Step 2: Scan and list unneeded/bloat packages
    flagged_bloat = inspect_unneeded_packages()
    
    # Step 3: Present optimization tiers
    print("\n" + "="*50)
    print(" SELECT OPTIMIZATION PROFILE")
    print("="*50)
    print("1. Light       (0.5x animations + Cache trimming)")
    print("2. Heavy       (Light fixes + Aggressive Doze mode + Disables scanned bloat apps)")
    print("3. Aggressive  (Heavy fixes + Zero animations + System log buffer clear)")
    
    choice = input("\nEnter choice (1, 2, or 3): ").strip()
    if choice not in ['1', '2', '3']:
        print("[-] Invalid choice selected. Exiting.")
        sys.exit(1)
        
    # Step 4: Terminal FPS diagnostic check
    print("\n" + "="*50)
    diag_resp = input("Run terminal FPS/Performance diagnostic check now? (y/n): ").strip().lower()
    if diag_resp == 'y':
        run_fps_check()
        proceed = input("\nAre you ready to proceed with the selected optimization sequence? (y/n): ").strip().lower()
        if proceed != 'y':
            print("Operation aborted by user.")
            sys.exit(0)
    else:
        print("Skipping diagnostic check...")

    # Step 5: Run chosen optimization category
    if choice == '1':
        optimize_light()
    elif choice == '2':
        optimize_heavy(flagged_bloat)
    elif choice == '3':
        optimize_aggressive(flagged_bloat)
        
    print("\n=== Optimization sequence completed successfully! ===")

if __name__ == "__main__":
    main()
