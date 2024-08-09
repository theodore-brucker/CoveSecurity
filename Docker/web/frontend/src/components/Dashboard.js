import React, { useState, useEffect } from 'react';
import { io } from "socket.io-client";
import ResizableCard from './ResizableCard';
import DataTable from './DataTable';
import TruncatedData from './TruncatedData';

const Dashboard = () => {
  // State variables
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
  const [currentPage, setCurrentPage] = useState(1);
  const [displayedSequences, setDisplayedSequences] = useState([]);
  const [totalSequences, setTotalSequences] = useState(0);
  const [allAnomalousSequences, setAllAnomalousSequences] = useState([]);
  const [selectedSequence, setSelectedSequence] = useState(null);
  const [userRequestedUpdate, setUserRequestedUpdate] = useState(false);


  // Utility functions
  const debounce = (func, wait) => {
    let timeout;
    return (...args) => {
      clearTimeout(timeout);
      timeout = setTimeout(() => func.apply(this, args), wait);
    };
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

  const fetchAnomalousSequences = async (page) => {
    try {
      const response = await fetch(`http://localhost:5000/anomalous_sequences?page=${page}`);
      const data = await response.json();
  
      if (data && Array.isArray(data.sequences)) {
        setDisplayedSequences(data.sequences);
        setTotalSequences(data.total || 0);
      } else {
        console.error('Unexpected data format:', data);
      }
    } catch (error) {
      console.error('Error fetching anomalous sequences:', error);
    }
  };

  const handlePreviousPage = () => {
    if (currentPage > 1) {
      setCurrentPage(prevPage => prevPage - 1);
    }
  };

  const handleNextPage = () => {
    if (currentPage * 5 < totalSequences) {
      setCurrentPage(prevPage => prevPage + 1);
    }
  };

  const handleRefreshAnomalousSequences = () => {
    setUserRequestedUpdate(true);
  };

  // useEffect hooks
  useEffect(() => {
    const newSocket = io("http://localhost:5000", {
      transports: ["websocket"],
    });

    const handleAnomalousSequencesUpdate = debounce((data) => {
      if (userRequestedUpdate && data && Array.isArray(data.sequences)) {
        setAllAnomalousSequences(data.sequences);
        setTotalSequences(data.total || 0);
        setDisplayedSequences(data.sequences.slice((currentPage - 1) * 5, currentPage * 5));
        setUserRequestedUpdate(false); // Reset the request flag after update
      }
    }, 300);

    newSocket.on("connect_error", (err) => {
      console.error("WebSocket connection error:", err);
    });

    newSocket.on("data_flow_health_update", setDataFlowHealth);
    newSocket.on("raw_sample_update", setRawSample);
    newSocket.on("processed_sample_update", setProcessedSample);
    newSocket.on("anomaly_numbers_update", setAnomalyNumbers);
    newSocket.on("training_status_update", setTrainingStatus);
    newSocket.on("anomalous_sequences_update", handleAnomalousSequencesUpdate);

    return () => {
      newSocket.disconnect();
    };
  }, [currentPage, userRequestedUpdate]);

  useEffect(() => {
    console.log(`Fetching anomalous sequences for page ${currentPage}`);
    fetchAnomalousSequences(currentPage);
  }, [currentPage]);

  const getValue = (row, key) => {
    if (key.includes('.')) {
      const keys = key.split('.');
      return keys.reduce((acc, part) => acc && acc[part], row);
    }
    return row[key];
  };

  // Card rendering functions
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
          <DataTable
            data={[rawSample]}
            columns={[
              { key: 'src_ip', label: 'Source IP', index: 0, render: (packet, human) => human.src_ip },
              { key: 'dst_ip', label: 'Destination IP', index: 1, render: (packet, human) => human.dst_ip },
              { key: 'protocol', label: 'Protocol', index: 5, render: (packet, human) => human.protocol },
              { key: 'src_port', label: 'Source Port', index: 6, render: (packet, human) => human.src_port },
              { key: 'dst_port', label: 'Destination Port', index: 7, render: (packet, human) => human.dst_port },
              { key: 'flags', label: 'Flags', render: (packet, human) => human.flags },
              // Add more columns as needed
            ]}
            isMultiSequence={false}
          />
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
          <DataTable
          data={[processedSample]}
          columns={[
            { key: 'src_ip', label: 'Source IP', index: 0, render: (packet, human) => human.src_ip },
            { key: 'dst_ip', label: 'Destination IP', index: 1, render: (packet, human) => human.dst_ip },
            { key: 'protocol', label: 'Protocol', index: 5, render: (packet, human) => human.protocol },
            { key: 'src_port', label: 'Source Port', index: 6, render: (packet, human) => human.src_port },
            { key: 'dst_port', label: 'Destination Port', index: 7, render: (packet, human) => human.dst_port },
            { key: 'flags', label: 'Flags', render: (packet, human) => human.flags },
            // Add more columns as needed
          ]}
          isMultiSequence={false}
          />
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

  const handleViewDetails = (sequence) => {
    if (sequence && sequence.human_readable) {
      setSelectedSequence(sequence.human_readable);
    } else {
      console.error('Invalid sequence data:', sequence);
      setSelectedSequence(null);
    }
  };

  const renderSequenceDetails = () => {
    if (!selectedSequence) return null;
  
    return (
      <div className="sequence-details-modal">
        <h3>Sequence Details</h3>
        <DataTable
          data={selectedSequence}
          columns={[
            { key: 'src_ip', label: 'Source IP', render: (packet) => packet.src_ip },
            { key: 'dst_ip', label: 'Destination IP', render: (packet) => packet.dst_ip },
            { key: 'protocol', label: 'Protocol', render: (packet) => packet.protocol },
            { key: 'src_port', label: 'Source Port', render: (packet) => packet.src_port },
            { key: 'dst_port', label: 'Destination Port', render: (packet) => packet.dst_port },
            { key: 'flags', label: 'Flags', render: (packet) => packet.flags },
          ]}
          isMultiSequence={true} // Set to true since it's an array of packets
        />
        <button onClick={() => setSelectedSequence(null)}>Close</button>
      </div>
    );
  };

  const renderAnomalousSequencesCard = () => (
    <div key="anomalousSequences" className="dashboard-item">
      <ResizableCard title="Anomalous Sequences">
        {displayedSequences.length > 0 ? (
          <div>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Sequence ID</th>
                  <th>Reconstruction Error</th>
                  <th>Is Anomaly</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {displayedSequences.map((sequence) => (
                  <tr key={sequence.id}>
                    <td>{sequence.id}</td>
                    <td>{sequence.reconstruction_error}</td>
                    <td>{sequence.is_anomaly ? 'Yes' : 'No'}</td>
                    <td>
                      <button onClick={() => handleViewDetails(sequence)}>View Details</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="pagination-controls">
              <button className="themed_button" onClick={handlePreviousPage} disabled={currentPage === 1}>Previous</button>
              <span>Page {currentPage}</span>
              <button className="themed_button" onClick={handleNextPage} disabled={currentPage * 5 >= totalSequences}>Next</button>
            </div>
            <button className="refresh-button" onClick={handleRefreshAnomalousSequences}>Refresh Data</button>
          </div>
        ) : (
          <p className="loading">No anomalous sequences detected yet...</p>
        )}
      </ResizableCard>
    </div>
  );

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

  // Main render function
  return (
    <div className="container">
      <header className="header">
        <h1>MLSEC</h1>
      </header>
      <div className="dashboard">
        {[
          renderDataFlowHealthCard(),
          renderAnomalyNumbersCard(),
          renderRawSampleCard(),
          renderProcessedSampleCard(),
          renderTrainModelCard(),
          renderAnomalousSequencesCard()
        ]}
      </div>
      {renderSequenceDetails()}
    </div>
  );
};

export default Dashboard;