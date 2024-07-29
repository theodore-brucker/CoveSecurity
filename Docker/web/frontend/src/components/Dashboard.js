import React, { useState, useEffect } from 'react';
import { io } from "socket.io-client";
import ResizableCard from './ResizableCard';
import logger from '../utils/logger';

const truncateString = (str, maxLength) => {
  if (typeof str !== 'string') {
    str = JSON.stringify(str);
  }
  if (!str || str.length <= maxLength) return str;
  return str.slice(0, maxLength) + '...';
};

const TruncatedData = ({ data, maxLength = 10 }) => {
  const [isExpanded, setIsExpanded] = useState(false);

  if (typeof data !== 'string') {
    data = JSON.stringify(data);
  }

  if (!data || data.length <= maxLength) {
    return <span className="data-value">{data}</span>;
  }

  return (
    <div>
      <span className="data-value">
        {isExpanded ? data : truncateString(data, maxLength)}
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

const Dashboard = () => {
  const [health, setHealth] = useState({ status: 'Unknown' });
  const [rawSample, setRawSample] = useState(null);
  const [processedSample, setProcessedSample] = useState(null);
  const [anomalyNumbers, setAnomalyNumbers] = useState({ total: 0, normal: 0, anomalous: 0 });
  const [trainingStatus, setTrainingStatus] = useState('');
  const [socket, setSocket] = useState(null);
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [dataFlowHealth, setDataFlowHealth] = useState({
    backend: { status: 'Unknown', last_update: null },
    raw: { status: 'Unknown', last_update: null },
    processed: { status: 'Unknown', last_update: null },
    prediction: { status: 'Unknown', last_update: null }
  });

  const [cardOrder, setCardOrder] = useState(['dataFlowHealth', 'anomalyNumbers', 'rawSample', 'processedSample', 'trainModel']);
  const [draggedCard, setDraggedCard] = useState(null);

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

  const handleTrainModel = async () => {
    if (!startDate || !endDate) {
      setTrainingStatus('Start Date and End Date are required');
      return;
    }

    const startTimestamp = new Date(startDate).getTime();
    const endTimestamp = new Date(endDate).getTime();
    if (isNaN(startTimestamp) || isNaN(endTimestamp)) {
      setTrainingStatus('Invalid dates provided');
      return;
    }

    setTrainingStatus('Starting training...');
    socket.emit('train_model', { startDate: startTimestamp, endDate: endTimestamp });
  };

  useEffect(() => {
    const newSocket = io("http://localhost:5000", {
      transports: ["websocket"],
    });
    newSocket.on("data_flow_health_update", (data) => {
      setDataFlowHealth(data);
    });

    newSocket.on("connect", () => {
      console.log("Connected to WebSocket");
    });

    newSocket.on("health_update", (data) => {
      setHealth(data);
    });

    newSocket.on("raw_sample_update", (data) => {
      setRawSample(data);
    });

    newSocket.on("processed_sample_update", (data) => {
      setProcessedSample(data);
    });

    newSocket.on("anomaly_numbers_update", (data) => {
      setAnomalyNumbers(data);
    });

    newSocket.on("training_status_update", (data) => {
      setTrainingStatus(data.status);
    });

    setSocket(newSocket);

    return () => {
      newSocket.disconnect();
    };
  }, []);

  const handleDragStart = (index, e) => {
    setDraggedCard(index);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', index);
  };

  const handleDragOver = (index, e) => {
    e.preventDefault();
    if (draggedCard === null) return;
    if (draggedCard !== index) {
      const newOrder = [...cardOrder];
      const [removed] = newOrder.splice(draggedCard, 1);
      newOrder.splice(index, 0, removed);
      setCardOrder(newOrder);
      setDraggedCard(index);
    }
  };

  const handleDragEnd = () => {
    setDraggedCard(null);
  };

  const renderCard = (cardType, index) => {
    switch (cardType) {
      case 'dataFlowHealth':
        return (
          <ResizableCard 
            key={cardType} 
            title="Data Flow Health" 
            onDragStart={handleDragStart} 
            onDragEnd={handleDragEnd}
            index={index}
          >
            <div>
            {Object.entries(dataFlowHealth).map(([key, value]) => (
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
        );
      case 'anomalyNumbers':
        return (
          <ResizableCard 
            key={cardType} 
            title="Anomaly Numbers" 
            onDragStart={handleDragStart} 
            onDragEnd={handleDragEnd}
            index={index}
          >
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
        );
      case 'rawSample':
        return (
          <ResizableCard 
            key={cardType} 
            title="Raw Data Sample" 
            onDragStart={handleDragStart} 
            onDragEnd={handleDragEnd}
            index={index}
          >
            {rawSample ? (
              <div>
                <p><span className="data-label">Time Stamp:</span> <TruncatedData data={rawSample ? rawSample.time : ''} /></p>
                <p><span className="data-label">Value:</span> <TruncatedData data={rawSample ? rawSample.data: ''} /></p>
              </div>
            ) : (
            <p className="loading">Loading raw data sample...</p>
            )}
          </ResizableCard>
        );
      case 'processedSample':
        return (
          <ResizableCard 
            key={cardType} 
            title="Processed Data Sample" 
            onDragStart={handleDragStart} 
            onDragEnd={handleDragEnd}
            index={index}
          >
            {processedSample ? (
              <div>
                <p><span className="data-label">Time Stamp:</span> <TruncatedData data={processedSample ? processedSample.timestamp : ''} /></p>
                <p><span className="data-label">Value:</span> <TruncatedData data={processedSample ? processedSample.features : ''} /></p>
              </div>
            ) : (
              <p className="loading">Loading processed data sample...</p>
            )}
          </ResizableCard>
        );
      case 'trainModel':
        return (
          <ResizableCard 
            key={cardType} 
            title="Model Training" 
            onDragStart={handleDragStart} 
            onDragEnd={handleDragEnd}
            index={index}
          >
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
            <button className="train-button" onClick={handleTrainModel}>Train Model</button>
            {trainingStatus && <p className="training-status">{trainingStatus}</p>}
          </ResizableCard>
        );
      default:
        return null;
    }
  };


  return (
    <div className="container">
      <header className="header">
        <h1>Command Center</h1>
      </header>
      <div className="dashboard">
        {cardOrder.map((cardType, index) => (
          <div 
            key={cardType} 
            onDragOver={(e) => handleDragOver(index, e)}
          >
            {renderCard(cardType, index)}
          </div>
        ))}
      </div>
    </div>
  );
};

export default Dashboard;
