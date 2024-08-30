# Cove Security

## Mission Statement

My mission is to bring power back to the average person on the internet.

## Description
Cove Security learns intricate network traffic patterns and detects novel and known attacks with increasing accuracy as it analyzes and learns a better and better representation of the network. By consuming only header-level packet information Cove Security can detect attacks that are currently undetected by traditional rule-based systems at a fraction of the compute cost.  

Cybersecurity companies currently charge an unimaginable amount of money for products that are just now catching up to modern machine learning techniques.

## Getting Started
### Recommended Hardware and Versioning

#### Resources  
CPU cores: 4  
Memory: 4 Gb  
Storage: 15 Gb

#### Versioning on Windows with WSL and Docker Desktop
Operating System: Windows 11 Home 23H2  
Ubuntu: 22.04.1 LTS (via WSL if needed)  
Docker Desktop Version: 4.32.0  
Docker Engine Version: 27.0.3  
Docker Compose Version: v2.28.1

#### Versioning on Docker for Linux
Operating System: Ubuntu 22.04.1 LTS or equivalent  
Docker Engine Version: 27.0.3  
Docker Compose Version: v2.28.1

### Download and Run
Installation Steps (from terminal)
1. Clone the repo  
   ```git clone https://github.com/theodore-brucker/Cove-Security.git```
2. Go into the folder  
   ```cd Cove-Security```
3. Open docker-compose.yml and view the variables. This file handles the networking between the services.
4. Build and Start Docker Containers
Docker Compose to build and start all necessary services:  
```docker-compose build```  
```docker-compose up -d```  

5. Once the services are running, you can access the web interface:  
Dashboard: http://localhost:3000  
Database UI: http://localhost:28081

6. Monitor Logs and Services  
You can monitor the logs of all services using:  
```docker-compose logs -f```  
This will help you track real-time logs and diagnose any issues that arise during the execution.

## Troubleshooting

#### Reach out to me at theodore.brucker@gmail.com if you need help. I'm happy to help you get everything set up.

#### Docker Startup Issues  
If any service fails to start, check the service dependencies and ensure that the required environment variables are correctly configured. The logs in the terminal will show what is wrong.

#### Data Persistence
MongoDB: By default, MongoDB data is persisted using a Docker volume. If you want to start with a fresh database on every restart, comment out the 2 lines in the docker-compose.yml file for the mongodb_data volume.


## Design Concepts

#### Autoencoder for Anomaly Detection

#### Kafka to enable Real-time Analysis

#### Docker for Scalability and Orchestration

#### Pytorch for Model Training

#### TorchServe for Model Hosting

#### Compiled Python for Data Processing
