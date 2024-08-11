import React, { useState, useEffect, useCallback, useRef } from 'react';
import { io } from "socket.io-client";
import ResizableCard from './ResizableCard';
import DataTable from './DataTable';
import logo from './Expanded-Cove-Logo.jpg';


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

  const [allAnomalousSequences, setAllAnomalousSequences] = useState([]);
  const [displayedSequences, setDisplayedSequences] = useState([]);
  const [currentPage, setCurrentPage] = useState(1);
  
  const [sequencesPerPage] = useState(5);
  const [selectedSequence, setSelectedSequence] = useState(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  
  const anomalousSequencesRef = useRef([]);

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

  const handleRefreshAnomalousSequences = useCallback(async () => {
    setIsRefreshing(true);
    try {
      const response = await fetch('http://localhost:5000/anomalous_sequences');
      const data = await response.json();
      console.log('Fetched anomalous sequences:', data);

      // Only update if we have non-empty sequences
      if (data && Array.isArray(data.sequences) && data.sequences.length > 0) {
        // Append the new sequences to the existing list
        setAllAnomalousSequences(prevSequences => {
          const updatedSequences = [...prevSequences, ...data.sequences];
          updateDisplayedSequences(updatedSequences, 1); // Show first page after refresh
          return updatedSequences;
        });
        anomalousSequencesRef.current = [
          ...anomalousSequencesRef.current,
          ...data.sequences
        ];
        console.log('Updated state after refresh:', anomalousSequencesRef.current);
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
    console.log('Updating displayed sequences:', newDisplayedSequences);
    setDisplayedSequences(newDisplayedSequences);
  }, [sequencesPerPage]);

  const handleAnomalousSequencesUpdate = useCallback((data) => {
    console.log('Received anomalous sequences update:', data);
    console.log('Current sequences before update:', anomalousSequencesRef.current);

    if (data && Array.isArray(data.sequences) && data.sequences.length > 0) {
      // Append the new sequences to the existing list
      const updatedSequences = [
        ...anomalousSequencesRef.current,
        ...data.sequences
      ];

      anomalousSequencesRef.current = updatedSequences;
      setAllAnomalousSequences(updatedSequences);
      updateDisplayedSequences(updatedSequences, currentPage);

      console.log('Updated sequences after emission:', updatedSequences);
    } else {
      console.log('Received an empty or invalid update, no changes made.');
    }
  }, [currentPage, updateDisplayedSequences]);

  const handlePreviousPage = useCallback(() => {
    if (currentPage > 1) {
      const newPage = currentPage - 1;
      setCurrentPage(newPage);
      updateDisplayedSequences(allAnomalousSequences, newPage);
    }
  }, [currentPage, allAnomalousSequences, updateDisplayedSequences]);

  const handleNextPage = useCallback(() => {
    const maxPage = Math.ceil(allAnomalousSequences.length / sequencesPerPage);
    if (currentPage < maxPage) {
      const newPage = currentPage + 1;
      setCurrentPage(newPage);
      updateDisplayedSequences(allAnomalousSequences, newPage);
    }
  }, [currentPage, allAnomalousSequences, sequencesPerPage, updateDisplayedSequences]);

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
    newSocket.on("anomalous_sequences_update", handleAnomalousSequencesUpdate);

    handleRefreshAnomalousSequences();

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

  const renderAnomalousSequencesCard = () => (
    <div key="anomalousSequences" className="dashboard-item">
      <ResizableCard title="Anomalous Sequences">
        {selectedSequence ? (
          <SequenceDetails
            sequence={selectedSequence}
            onBack={() => setSelectedSequence(null)}
          />
        ) : (
          <div>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Sequence ID</th>
                  <th>Reconstruction Error</th>
                  <th>Is Anomaly</th>
                </tr>
              </thead>
              <tbody>
                {displayedSequences.map((sequence) => (
                  <tr key={sequence.id} onClick={() => setSelectedSequence(sequence)} style={{cursor: 'pointer'}}>
                    <td>{sequence.id}</td>
                    <td>{sequence.reconstruction_error.toFixed(4)}</td>
                    <td>{sequence.is_anomaly ? 'Yes' : 'No'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="pagination-controls">
              <button className="themed_button" onClick={handlePreviousPage} disabled={currentPage === 1}>Previous</button>
              <span>Page {currentPage} of {Math.ceil(allAnomalousSequences.length / sequencesPerPage)}</span>
              <button className="themed_button" onClick={handleNextPage} disabled={currentPage * sequencesPerPage >= allAnomalousSequences.length}>Next</button>
            </div>
            <button className="refresh-button" onClick={handleRefreshAnomalousSequences}>Refresh Data</button>
          </div>
        )}
      </ResizableCard>
    </div>
  );

  const SequenceDetails = ({ sequence, onBack }) => {
    // Prepare the data in the format expected by DataTable
    const formattedData = [{
      id: sequence.id,
      sequence: sequence.human_readable.map(packet => 
        // Create a dummy 'sequence' array to match the expected structure
        [packet.src_ip, packet.dst_ip, '', '', '', packet.protocol, packet.src_port, packet.dst_port]
      ),
      human_readable: sequence.human_readable
    }];
  
    return (
      <div>
        <button className="themed_button" onClick={onBack}>Back to List</button>
        <h3>Sequence ID: {sequence.id}</h3>
        <p>Reconstruction Error: {sequence.reconstruction_error.toFixed(4)}</p>
        <p>Is Anomaly: {sequence.is_anomaly ? 'Yes' : 'No'}</p>
        <DataTable
          data={formattedData}
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

  const renderLogoCard = () => (
    <div key="logoCard" className="dashboard-item logo-card">
      <img src={logo} alt="Cove Security Logo" className="dashboard-logo" />
    </div>
  );

  // Main render function
  return (
    <div className="container">
      {renderLogoCard()}
      <div className="dashboard">
        {[
          renderTrainModelCard(),
          renderAnomalyNumbersCard(),
          renderAnomalousSequencesCard(),
          renderDataFlowHealthCard(),
          renderRawSampleCard(),
          renderProcessedSampleCard()
        ]}
      </div>
    </div>
  );
};

export default Dashboard;
