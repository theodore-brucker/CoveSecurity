# forward_traffic.ps1
# This script forwards web traffic on ports 80 and 443 from Windows to WSL.
# It retrieves the WSL IP address and uses netsh to create port forwarding rules.

# Get the WSL IP address
$wsl_ip = (wsl hostname -I).Trim()

# Define the ports to forward
$ports = @(80, 443)

# Start a try-catch block to handle any errors
try {
    # Loop through each port and set up port forwarding
    foreach ($port in $ports) {
        netsh interface portproxy add v4tov4 listenport=$port listenaddress=0.0.0.0 connectport=$port connectaddress=$wsl_ip
    }
    Write-Host "Port forwarding successfully set up for ports $($ports -join ', ') to WSL IP: $wsl_ip"
}
catch {
    # Print any error that occurs during the execution
    Write-Host "An error occurred while setting up port forwarding: $_"
}

# Keep the PowerShell window open until the user presses Enter
Write-Host "Press Enter to exit..."
[void][System.Console]::ReadLine()
