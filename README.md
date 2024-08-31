# Cove Security

## Mission Statement
My mission is to push the boundary of free intrusion detection tools  
[Learn More](https://cove-security.com)

## Description
By consuming only header level packet information Cove Security is able to detect attacks that are currently undetected by traditional rule based systems at a fraction of the compute cost

## Prerequisites
### Docker and Docker Compose
Ensure [Docker Desktop](https://docs.docker.com/desktop/install/windows-install/) is installed. They have good documentation at the link.

## Quick Start
1. Clone the repository:  
```git clone https://github.com/theodore-brucker/CoveSecurity.git```  
```cd CoveSecurity/Docker/```  

2. Start the services:  
```docker-compose up```  

3. Access the dashboard:  
Dashboard UI: http://localhost:3000

1. Forward Windows Web Traffic to WSL:  
Open admin PowerShell session and cd to the clone of the repo, then run:  
```powershell -ExecutionPolicy Bypass -File .\forward_ports.ps1```

#### Extra commands
To stop the services gracefully  
```docker-compose down```

To monitor logs from all services   
```docker-compose logs -f```  


## Troubleshooting FAQ

#### Reach out to me at theodore.brucker@gmail.com if you need help. I'm happy to help you get everything set up.

### Recommended Versioning for Windows
Operating System: Windows 11 Home 23H2  
Ubuntu: 22.04.1 LTS (via WSL if needed)  
Docker Desktop Version: 4.32.0  
Docker Engine Version: 27.0.3  
Docker Compose Version: v2.28.1
### Recommended Versioning for Linux
Operating System: Ubuntu 22.04.1 LTS or equivalent  
Docker Engine Version: 27.0.3  
Docker Compose Version: v2.28.1

#### Docker Startup Issues  
If any service fails to start, check the service dependencies and ensure that the required environment variables are correctly configured.  
**The logs in the terminal will be your best friend**

#### Data Persistence
MongoDB: By default, MongoDB data is persisted using a Docker volume. If you want to start with a fresh database on every restart, comment out the 2 lines in the docker-compose.yml file for the mongodb_data volume.


## Design Concepts

#### Autoencoder for Anomaly Detection

#### Kafka to enable Real-time Analysis

#### Docker for Scalability and Orchestration

#### Pytorch for Model Training

#### TorchServe for Model Hosting

#### Compiled Python for Data Processing
