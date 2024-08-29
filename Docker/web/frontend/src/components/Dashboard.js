import React, { useState, useEffect, useCallback } from 'react';
import { io } from "socket.io-client";
import ResizableCard from './ResizableCard';
import DataTable from './DataTable';
import logo from './Expanded-Cove-Logo.jpg';
import axios from 'axios';
import AnomalousSequencesCard from './AnomalousSequencesCard';
import MongoDataCard from './MongoDataCard';
import TrafficRatioChart from './TrafficRatioChart';

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
    prediction: { status: 'Unknown', last_update: null }
  });

  const [allAnomalousSequences, setAllAnomalousSequences] = useState([]);
  const maxSequences = 1000; // Set a maximum number of sequences to keep
  const [displayedSequences, setDisplayedSequences] = useState([]);
  const [currentPage, setCurrentPage] = useState(1);
  const [sequencesPerPage] = useState(5);
  const [selectedSequence, setSelectedSequence] = useState(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  
  const [activeTab, setActiveTab] = useState('time');
  const [isMarkingNormal, setIsMarkingNormal] = useState(false);

  const [mongoData, setMongoData] = useState([]);
  const [isLoadingMongoData, setIsLoadingMongoData] = useState(false);
  const [totalPages, setTotalPages] = useState(1);
  const itemsPerPage = 10; // You can adjust this as needed

  // Utility functions
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

  const handleTrainingData = async (type) => {
    setTrainingStatus({ status: 'starting', progress: 0, message: 'Submitting training data...' });
    const formData = new FormData();

    if (type === 'pcap') {
      if (file) {
        formData.append('file', file);
      } else {
        setTrainingStatus({ status: 'error', progress: 0, message: 'Please provide a PCAP file' });
        return;
      }
    } else if (type === 'time') {
      if (startDate && endDate) {
        formData.append('startDate', new Date(startDate).getTime());
        formData.append('endDate', new Date(endDate).getTime());
      } else {
        setTrainingStatus({ status: 'error', progress: 0, message: 'Please provide start and end dates' });
        return;
      }
    } else {
      setTrainingStatus({ status: 'error', progress: 0, message: 'Invalid training data type' });
      return;
    }

    try {
      const response = await fetch('http://localhost:5000/training_data', {
        method: 'POST',
        body: formData,
      });

      if (response.ok) {
        const result = await response.json();
        setTrainingStatus({ status: 'submitted', progress: 0, message: 'Training data submitted successfully. Waiting for processing to begin.' });
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

  const handleRefreshAnomalousSequences = useCallback(async () => {
    setIsRefreshing(true);
    try {
      const response = await fetch('http://localhost:5000/anomalous_sequences');
      const data = await response.json();
      console.log('Fetched anomalous sequences:', data);

      if (data && Array.isArray(data.sequences) && data.sequences.length > 0) {
        setAllAnomalousSequences(prevSequences => {
          const updatedSequences = [...data.sequences, ...prevSequences].slice(0, maxSequences);
          console.log('Updated state after refresh:', updatedSequences);
          return updatedSequences;
        });
      } else {
        console.log('No new sequences fetched, keeping existing sequences.');
      }
    } catch (error) {
      console.error('Error fetching anomalous sequences:', error);
    } finally {
      setIsRefreshing(false);
    }
  }, []);

  const updateDisplayedSequences = useCallback((sequences, page) => {
    const startIndex = (page - 1) * sequencesPerPage;
    const endIndex = startIndex + sequencesPerPage;
    const newDisplayedSequences = sequences.slice(startIndex, endIndex);
    setDisplayedSequences(newDisplayedSequences);
  }, [sequencesPerPage]);

  const handleAnomalousSequencesUpdate = useCallback((data) => {
    if (data && Array.isArray(data.sequences) && data.sequences.length > 0) {
      setAllAnomalousSequences(prevSequences => {
        const updatedSequences = [...data.sequences, ...prevSequences].slice(0, maxSequences);
        return updatedSequences;
      });
    }
  }, []);

  useEffect(() => {
    updateDisplayedSequences(allAnomalousSequences, currentPage);
  }, [allAnomalousSequences, currentPage, updateDisplayedSequences]);

  const handlePreviousPage = useCallback(() => {
    setCurrentPage(prev => Math.max(prev - 1, 1));
  }, []);

  const handleNextPage = useCallback(() => {
    setCurrentPage(prev => Math.min(prev + 1, totalPages));
  }, [totalPages]);

  const handleMarkAsNormal = async (sequenceId) => {
    setIsMarkingNormal(true);
    try {
      await axios.post('http://localhost:5000/mark_as_normal', { _id: sequenceId });
      setAllAnomalousSequences(prevSequences => 
        prevSequences.filter(seq => seq.id !== sequenceId)
      );
      updateDisplayedSequences(
        allAnomalousSequences.filter(seq => seq.id !== sequenceId),
        currentPage
      );
    } catch (error) {
      console.error('Error marking sequence as normal:', error);
    } finally {
      setIsMarkingNormal(false);
    }
  };

  const handleTrainWithLabeledData = async () => {
    try {
      const response = await axios.post('http://localhost:5000/train_with_labeled_data');
      setTrainingStatus({ status: 'in_progress', progress: 0, message: 'Training with labeled data initiated' });
    } catch (error) {
      setTrainingStatus({ status: 'error', progress: 0, message: `Error: ${error.message}` });
    }
  };

  const fetchMongoData = useCallback(async (page = currentPage) => {
    setIsLoadingMongoData(true);
    try {
      const params = {
        page: page,
        per_page: itemsPerPage
      };
      if (startDate) params.start_date = startDate;
      if (endDate) params.end_date = endDate;

      console.log('Fetching data with params:', params);
      const response = await axios.get('http://localhost:5000/api/data', { params });
      console.log('Received data:', response.data);
      setMongoData(response.data.data);
      setTotalPages(response.data.total_pages);
      setCurrentPage(response.data.current_page);
    } catch (error) {
      console.error('Error fetching MongoDB data:', error);
    } finally {
      setIsLoadingMongoData(false);
    }
  }, [startDate, endDate, itemsPerPage, currentPage]);

  useEffect(() => {
    fetchMongoData(currentPage);
  }, [fetchMongoData, currentPage]);

  useEffect(() => {
    const newSocket = io("http://localhost:5000", {
      transports: ["websocket"],
      reconnection: true,
      reconnectionAttempts: Infinity,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 5000,
      timeout: 20000,
    });

    newSocket.on("connect", () => {
      console.log("Connected to server");
      handleRefreshAnomalousSequences();
    });

    newSocket.on("disconnect", (reason) => {
      console.log("Disconnected from server:", reason);
    });

    newSocket.on("connect_error", (err) => {
      console.error("WebSocket connection error:", err);
    });

    newSocket.on("data_flow_health_update", setDataFlowHealth);
    newSocket.on("raw_sample_update", setRawSample);
    newSocket.on("processed_sample_update", setProcessedSample);
    newSocket.on("traffic_ratio_update", (data) => {
      setAnomalyNumbers({
          total: data.total_count,
          normal: data.normal_count,
          anomalous: data.anomalous_count
      });
    });

    newSocket.on("training_status_update", setTrainingStatus);
    newSocket.on("anomalous_sequences_update", handleAnomalousSequencesUpdate);

    return () => {
      newSocket.disconnect();
    };
  }, [handleAnomalousSequencesUpdate, handleRefreshAnomalousSequences]);

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

  const renderTrafficRatioChart = () => (
    <div key="trafficRatio" className="dashboard-item">
        <ResizableCard title="Traffic Ratio">
            <TrafficRatioChart 
                normal={anomalyNumbers.normal} 
                anomalous={anomalyNumbers.anomalous} 
            />
        </ResizableCard>
    </div>
  );

  const renderAnomalyNumbersCard = () => (
    <div key="anomalyNumbers" className="dashboard-item">
        <ResizableCard title="Anomaly Numbers">
            {anomalyNumbers ? (
                <div>
                    <p><span className="data-label">Total Predictions:</span> {anomalyNumbers.total}</p>
                    <p><span className="data-label">Normal Sequences:</span> {anomalyNumbers.normal}</p>
                    <p><span className="data-label">Anomalous Sequences:</span> {anomalyNumbers.anomalous}</p>
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
        <div className="training-tabs">
          <button className={`tab-button ${activeTab === 'time' ? 'active' : ''}`} onClick={() => setActiveTab('time')}>Time Window</button>
          <button className={`tab-button ${activeTab === 'pcap' ? 'active' : ''}`} onClick={() => setActiveTab('pcap')}>PCAP File</button>
          <button className={`tab-button ${activeTab === 'labeled' ? 'active' : ''}`} onClick={() => setActiveTab('labeled')}>Labeled Data</button>
        </div>
        
        <div className="tab-content">
          {activeTab === 'time' && (
            <div className="time-window-section">
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
              <button className="themed_button" onClick={() => handleTrainingData('time')}>Submit Time Window</button>
            </div>
          )}
          
          {activeTab === 'pcap' && (
            <div className="pcap-upload-section">
              <div className="themed_file_input">
                <label>
                  Upload PCAP file
                  <input type="file" accept=".pcap" onChange={handleFileChange} />
                </label>
              </div>
              {file && <span className="file-name">{file.name}</span>}
              <button className="themed_button" onClick={() => handleTrainingData('pcap')} disabled={!file}>Submit PCAP File</button>
            </div>
          )}
          
          {activeTab === 'labeled' && (
            <div className="labeled-data-section">
              <button className="themed_button" onClick={handleTrainWithLabeledData}>Train with Labeled Data</button>
            </div>
          )}
        </div>
        
        <button className="start-training-button" onClick={handleStartTrainingJob}>
          Start Training Job
        </button>
        
        {trainingStatus.status && (
          <TrainingStatusDisplay 
            status={trainingStatus.status} 
            progress={trainingStatus.progress} 
            message={trainingStatus.message} 
          />
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

  const renderLogoCard = () => (
    <div key="logoCard" className="dashboard-item logo-card">
      <img src={logo} alt="Cove Security Logo" className="dashboard-logo" />
    </div>
  );

  // Main render function
  return (
    <div className="container">
      <h1>MLSec</h1>
      <h2>Dashboard</h2>
      <div className="dashboard">
        {renderTrainModelCard()}
        {renderDataFlowHealthCard()}
        {renderAnomalyNumbersCard()}
        {renderTrafficRatioChart()}
        <AnomalousSequencesCard />
        <MongoDataCard />
      </div>
    </div>
  );
};

export default Dashboard;
