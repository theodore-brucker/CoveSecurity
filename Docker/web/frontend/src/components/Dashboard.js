import React, { useState, useEffect } from 'react';
import { io } from "socket.io-client";
import ResizableCard from './ResizableCard';

//
// MAIN COMPONENT
// 

const Dashboard = () => {

  //
  // VARIABLES
  //
  const [rawSample, setRawSample] = useState(null);
  const [processedSample, setProcessedSample] = useState(null);
  const [anomalyNumbers, setAnomalyNumbers] = useState({ total: 0, normal: 0, anomalous: 0 });
  const [trainingStatus, setTrainingStatus] = useState({
    status: '',
    progress: 0,
    message: ''
  });
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [file, setFile] = useState(null);
  const [dataFlowHealth, setDataFlowHealth] = useState({
    backend: { status: 'loading', last_update: null },
    raw: { status: 'Unknown', last_update: null },
    processed: { status: 'Unknown', last_update: null },
    training: { status: 'Unknown', last_update: null },
    prediction: { status: 'Unknown', last_update: null }
  });

  // VARIABLES FOR ANOMALOUS PACKET VIEWING
  const [currentPage, setCurrentPage] = useState(1);
  const [displayedPackets, setDisplayedPackets] = useState([]);
  const [totalPackets, setTotalPackets] = useState(0);

  //
  // UTILITY FUNCTIONS AND HANDLERS
  // 

  const truncateString = (str, maxLength) => {
    if (str == null) {
      return '';
    }
    if (typeof str !== 'string') {
      try {
        str = JSON.stringify(str);
      } catch (e) {
        return ''; // return empty string if data cannot be stringified
      }
    }
    if (!str || str.length <= maxLength) return str;
    return str.slice(0, maxLength) + '...';
  };

  const TruncatedData = ({ data, maxLength = 12 }) => {
    const [isExpanded, setIsExpanded] = useState(false);

    let displayData;
    if (data == null) {
      displayData = '';
    } else if (typeof data !== 'string') {
      try {
        displayData = JSON.stringify(data);
      } catch (e) {
        displayData = ''; // fallback if data cannot be stringified
      }
    } else {
      displayData = data;
    }

    if (displayData.length <= maxLength) {
      return <span className="data-value">{displayData}</span>;
    }

    return (
      <div>
        <span className="data-value">
          {isExpanded ? displayData : truncateString(displayData, maxLength)}
        </span>
        <button 
          className="toggle-button" 
          onClick={() => setIsExpanded(!isExpanded)}
        >
          {isExpanded ? 'Show Less' : 'Show More'}
        </button>
      </div>
    );
  };

  const getStatusClass = (status) => {
    if (status === 'healthy') return 'status-healthy';
    if (status === 'Unknown') return 'loading';
    return 'status-unhealthy';
  };

  const formatLastUpdate = (timestamp) => {
    if (!timestamp) return '';
    const date = new Date(timestamp);
    return date.toLocaleTimeString();
  };

  const handleFileChange = (e) => {
    setFile(e.target.files[0]);
  };

  const handleTrainingData = async () => {
    setTrainingStatus({ status: 'starting', progress: 0, message: 'Submitting training data...' });
    const formData = new FormData();

    if (file) {
      formData.append('file', file);
    } else if (startDate && endDate) {
      formData.append('startDate', new Date(startDate).getTime());
      formData.append('endDate', new Date(endDate).getTime());
    } else {
      setTrainingStatus({ status: 'error', progress: 0, message: 'Please provide either a PCAP file or start and end dates' });
      return;
    }

    try {
      const response = await fetch('http://localhost:5000/training_data', {
        method: 'POST',
        body: formData,
      });

      if (response.ok) {
        const result = await response.json();
        setTrainingStatus({ status: 'ready', progress: 100, message: 'Training data submitted successfully.' });
      } else {
        const errorText = await response.text();
        setTrainingStatus({ status: 'error', progress: 0, message: `Error: ${errorText}` });
      }
    } catch (error) {
      setTrainingStatus({ status: 'error', progress: 0, message: `Error: ${error.message}` });
    }
  };

  const handleStartTrainingJob = async () => {
    try {
      const response = await fetch('http://localhost:5000/training_start', {
        method: 'POST',
      });

      if (response.ok) {
        const result = await response.json();
        setTrainingStatus({ status: 'in_progress', progress: 0, message: result.message });
      } else {
        const errorText = await response.text();
        setTrainingStatus({ status: 'error', progress: 0, message: `Error: ${errorText}` });
      }
    } catch (error) {
      setTrainingStatus({ status: 'error', progress: 0, message: `Error: ${error.message}` });
    }
  };

  //
  // USE EFFECTS
  // 

  useEffect(() => {
    const newSocket = io("http://localhost:5000", {
      transports: ["websocket"],
    });
  
    newSocket.on("connect_error", (err) => {
      console.error("WebSocket connection error:", err);
    });
    
    newSocket.on("data_flow_health_update", setDataFlowHealth);
    newSocket.on("raw_sample_update", setRawSample);
    newSocket.on("processed_sample_update", setProcessedSample);
    newSocket.on("anomaly_numbers_update", setAnomalyNumbers);
    newSocket.on("training_status_update", setTrainingStatus);
    newSocket.on("anomalous_packets_update", (data) => {
      console.log("Received anomalous packets update:", data);
      setTotalPackets(data.total);
      setDisplayedPackets(data.packets);
    });
  
    return () => {
      newSocket.disconnect();
    };
  }, []);

  useEffect(() => {
    fetchAnomalousPackets(currentPage);
  }, [currentPage]);


  const fetchAnomalousPackets = async (page) => {
    console.log(`Fetching anomalous packets for page ${page}`);
    try {
      const response = await fetch(`http://localhost:5000/anomalous_packets?page=${page}`);
      const data = await response.json();
      console.log("Fetched anomalous packets:", data);
      setDisplayedPackets(data.packets);
      setTotalPackets(data.total);
    } catch (error) {
      console.error('Error fetching anomalous packets:', error);
    }
  };
  
  const handlePreviousPage = () => {
    if (currentPage > 1) {
      setCurrentPage(prevPage => prevPage - 1);
    }
  };
  
  const handleNextPage = () => {
    if (currentPage * 5 < totalPackets) {
      setCurrentPage(prevPage => prevPage + 1);
    }
  };


  //
  // FUNCTIONS TO ENCAPSULATE ALL OF THE CARDS ON THE DASHBOARD
  // 


  const renderDataFlowHealthCard = () => (
    <div key="dataFlowHealth" className="dashboard-item">
      <ResizableCard title="Data Flow Health">
        <div>
          {dataFlowHealth && Object.entries(dataFlowHealth).map(([key, value]) => (
            <p key={key}>
              <span className="data-label">{key.charAt(0).toUpperCase() + key.slice(1)}:</span>
              <span className={getStatusClass(value.status)}> {value.status}</span>
              {value.last_update && (
                <span className="last-update-time"> as of <span className="time">{formatLastUpdate(value.last_update)}</span></span>
              )}
            </p>
          ))}
        </div>
      </ResizableCard>
    </div>
  );

  const renderAnomalyNumbersCard = () => (
    <div key="anomalyNumbers" className="dashboard-item">
      <ResizableCard title="Anomaly Numbers">
        {anomalyNumbers ? (
          <div>
            <p><span className="data-label">Total Predictions:</span> {anomalyNumbers.total}</p>
            <p><span className="data-label">Normal Entries:</span> {anomalyNumbers.normal}</p>
            <p><span className="data-label">Anomalous Entries:</span> {anomalyNumbers.anomalous}</p>
          </div>
        ) : (
          <p className="loading">Loading anomaly data...</p>
        )}
      </ResizableCard>
    </div>
  );

  const renderRawSampleCard = () => (
    <div key="rawSample" className="dashboard-item">
      <ResizableCard title="Raw Data Sample">
        {rawSample ? (
          <div>
            <p><span className="data-label">Packet ID:</span> <TruncatedData data={rawSample.id} /></p>
            <p><span className="data-label">Time Stamp:</span> <TruncatedData data={rawSample.time} /></p>
            <p><span className="data-label">Data:</span> <TruncatedData data={rawSample.data} /></p>
            <p><span className="data-label">Human Readable:</span></p>
            <ul>
              {rawSample.human_readable && Object.entries(rawSample.human_readable).map(([key, value]) => (
                <li key={key}><span className="data-label">{key}:</span> <TruncatedData data={value} /></li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="loading">Loading raw data sample...</p>
        )}
      </ResizableCard>
    </div>
  );

  const renderProcessedSampleCard = () => (
    <div key="processedSample" className="dashboard-item">
      <ResizableCard title="Processed Data Sample">
        {processedSample ? (
          <table className="data-table">
            <thead>
              <tr>
                <th>Packet ID</th>
                <th>Timestamp</th>
                <th>Features</th>
                <th>src_ip</th>
                <th>dst_ip</th>
                <th>protocol</th>
                <th>flags</th>
                <th>src_port</th>
                <th>dst_port</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><TruncatedData data={processedSample.id} /></td>
                <td><TruncatedData data={processedSample.timestamp} /></td>
                <td><TruncatedData data={processedSample.features.join(', ')} /></td>
                <td><TruncatedData data={processedSample.human_readable.src_ip} /></td>
                <td><TruncatedData data={processedSample.human_readable.dst_ip} /></td>
                <td><TruncatedData data={processedSample.human_readable.protocol} /></td>
                <td><TruncatedData data={processedSample.human_readable.flags} /></td>
                <td><TruncatedData data={processedSample.human_readable.src_port} /></td>
                <td><TruncatedData data={processedSample.human_readable.dst_port} /></td>
              </tr>
            </tbody>
          </table>
        ) : (
          <p className="loading">Loading processed data sample...</p>
        )}
      </ResizableCard>
    </div>
  );

  const renderTrainModelCard = () => (
    <div key="trainModel" className="dashboard-item">
      <ResizableCard title="Model Training">
        <div className="date-selection">
          <label>
            Start Date:
            <input type="datetime-local" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
          </label>
          <label>
            End Date:
            <input type="datetime-local" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
          </label>
        </div>
        <div className="button-container">
          <label className="themed_button">
            Upload PCAP file:
            <input type="file" accept=".pcap" onChange={handleFileChange} />
          </label>
          <button className="themed_button" onClick={handleTrainingData}>Submit Training Data</button>
        </div>
        {trainingStatus.status && (
          <TrainingStatusDisplay 
            status={trainingStatus.status} 
            progress={trainingStatus.progress} 
            message={trainingStatus.message} 
          />
        )}
        {(trainingStatus.status && trainingStatus.status !== 'starting') && (
          <button className="themed_button" onClick={handleStartTrainingJob}>
            Start Training Job
          </button>
        )}
      </ResizableCard>
    </div>
  );

  const renderAnomalousPacketsCard = () => {
    console.log("Rendering Anomalous Packets Card");
    console.log("displayedPackets:", displayedPackets);
  
    return (
      <div key="anomalousPackets" className="dashboard-item">
        <ResizableCard title="Anomalous Packets">
          {displayedPackets.length > 0 ? (
            <div>
              {displayedPackets.map((packet, index) => {
                console.log(`Rendering packet ${index}:`, packet);
                return (
                  <div key={index}>
                    <p><span className="data-label">Packet ID:</span> <TruncatedData data={packet.id} /></p>
                    {packet.timestamp && <p><span className="data-label">Time Stamp:</span> <TruncatedData data={packet.timestamp} /></p>}
                    {packet.data && <p><span className="data-label">Data:</span> <TruncatedData data={packet.data} /></p>}
                    <p><span className="data-label">Human Readable:</span></p>
                    <ul>
                      {packet.human_readable && Object.entries(packet.human_readable).map(([key, value]) => {
                        console.log(`Rendering human readable entry: ${key}:`, value);
                        return (
                          <li key={key}><span className="data-label">{key}:</span> <TruncatedData data={value} /></li>
                        );
                      })}
                    </ul>
                  </div>
                );
              })}
              <div className="pagination-controls">
                <button className="themed_button" onClick={handlePreviousPage} disabled={currentPage === 1}>Previous</button>
                <span>Page {currentPage}</span>
                <button className="themed_button" onClick={handleNextPage} disabled={currentPage * 5 >= totalPackets}>Next</button>
              </div>
            </div>
          ) : (
            <p className="loading">No anomalous packets detected yet...</p>
          )}
        </ResizableCard>
      </div>
    );
  };

  const TrainingStatusDisplay = ({ status, progress, message }) => (
    <div className="training-status">
      <p><strong>Status:</strong> {status}</p>
      <div className="progress-bar">
        <div 
          className="progress" 
          style={{width: `${progress}%`}}
        ></div>
      </div>
      <p><strong>Progress:</strong> {progress}%</p>
      <p><strong>Message:</strong> {message}</p>
    </div>
  );

  //
  // FUNCTION TO RENDER ALL OF THE CARDS ON THE DASHBOARD
  // 

  const renderCard = (cardType) => {
    switch (cardType) {
      case 'dataFlowHealth':
        return renderDataFlowHealthCard();
      case 'anomalyNumbers':
        return renderAnomalyNumbersCard();
      case 'rawSample':
        return renderRawSampleCard();
      case 'processedSample':
        return renderProcessedSampleCard();
      case 'trainModel':
        return renderTrainModelCard();
      case 'anomalousPackets':
        return renderAnomalousPacketsCard();
      default:
        return null;
    }
  };

  return (
    <div className="container">
      <header className="header">
        <h1>MLSEC</h1>
      </header>
      <div className="dashboard">
        {['dataFlowHealth', 'anomalyNumbers', 'rawSample', 'processedSample', 'trainModel', 'anomalousPackets'].map((cardType) => (
          renderCard(cardType)
        ))}
      </div>
    </div>
  );
};

export default Dashboard;