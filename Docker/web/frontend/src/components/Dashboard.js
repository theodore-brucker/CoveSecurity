import React, { useState, useEffect } from 'react';
import logger from '../utils/logger';



const truncateString = (str, maxLength) => {
  if (str.length <= maxLength) return str;
  return str.slice(0, maxLength) + '...';
};

const TruncatedData = ({ data, maxLength = 10 }) => {
  const [isExpanded, setIsExpanded] = useState(false);

  if (data.length <= maxLength) {
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
  const [ratio, setRatio] = useState(null);
  const [health, setHealth] = useState(null);
  const [rawSample, setRawSample] = useState(null);
  const [processedSample, setProcessedSample] = useState(null);
  const [predictionSample, setPredictionSample] = useState(null);
  const [error, setError] = useState(null);
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [trainingStatus, setTrainingStatus] = useState('');
  const [anomalyNumbers, setAnomalyNumbers] = useState({ total: 0, normal: 0, anomalous: 0 });
  const [isUpdatingAnomalyNumbers, setIsUpdatingAnomalyNumbers] = useState(false);

  const fetchData = async (url, setStateFunction, logMessage, fillerValue) => {
    logger.log(logMessage);
    try {
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const data = await response.json();
      logger.log(`Fetched data:`, data);
      setStateFunction(data);
    } catch (error) {
      logger.error(`Error fetching data from ${url}:`, error);
      setStateFunction(fillerValue);
      setError(`Error fetching data from ${url}: ${error.message}`);
    }
  };

  const handleTrainModel = async () => {
    setTrainingStatus('Starting training...');
    try {
      // Convert the dates to timestamps
      const startTimestamp = new Date(startDate).getTime();
      const endTimestamp = new Date(endDate).getTime();
  
      const response = await fetch('http://localhost:5000/train_model', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ startDate: startTimestamp, endDate: endTimestamp }),
      });
      const data = await response.json();
      setTrainingStatus(data.message);
    } catch (error) {
      console.error('Error training model:', error);
      setTrainingStatus('Error training model');
    }
  };
  
  const getHealthStatusClass = (status) => {
    return status === 'OK' ? 'status-healthy' : 'status-unhealthy';
  };

  const startUpdatingAnomalyNumbers = () => {
    setIsUpdatingAnomalyNumbers(true);
    const eventSource = new EventSource('http://localhost:5000/anomaly_numbers');
    
    eventSource.onmessage = (event) => {
      const data = JSON.parse(event.data);
      setAnomalyNumbers(data);
    };

    eventSource.onerror = (error) => {
      console.error('EventSource failed:', error);
      eventSource.close();
      setIsUpdatingAnomalyNumbers(false);
    };

    return () => {
      eventSource.close();
      setIsUpdatingAnomalyNumbers(false);
    };
  };

  const stopUpdatingAnomalyNumbers = () => {
    setIsUpdatingAnomalyNumbers(false);
  };

  useEffect(() => {
      fetchData("http://localhost:5000/ratio", setRatio, 'Fetching ratio data...', { anomalous: 0, normal: 0, ratio: 0 });
      fetchData("http://localhost:5000/health", setHealth, 'Fetching health check data...', { status: 'Unknown' });
      fetchData("http://localhost:5000/raw_sample", setRawSample, 'Fetching raw data sample...', { key: 'N/A', value: 'N/A' });
      fetchData("http://localhost:5000/processed_sample", setProcessedSample, 'Fetching processed data sample...', { key: 'N/A', value: 'N/A' });
      fetchData("http://localhost:5000/prediction_sample", setPredictionSample, 'Fetching prediction data sample...', { key: 'N/A', value: 'N/A' });
    }, []);
  
    return (
      <div className="container">
        <header className="header">
          <h1>Command Center</h1>
          <div className={`health-status ${health ? getHealthStatusClass(health.status) : ''}`}>
            Status: {health ? (
              <span>{health.status}</span>
            ) : (
              <span className="loading">Loading...</span>
            )}
          </div>
        </header>
        <div className="dashboard">
          <div className="dashboard-item anomaly-numbers">
            <h2>Anomaly Numbers</h2>
            <p><span className="data-label">Total Predictions:</span> {anomalyNumbers.total}</p>
            <p><span className="data-label">Normal Entries:</span> {anomalyNumbers.normal}</p>
            <p><span className="data-label">Anomalous Entries:</span> {anomalyNumbers.anomalous}</p>
            <button 
              onClick={isUpdatingAnomalyNumbers ? stopUpdatingAnomalyNumbers : startUpdatingAnomalyNumbers}
              className={isUpdatingAnomalyNumbers ? 'stop-update-button' : 'start-update-button'}
            >
              {isUpdatingAnomalyNumbers ? 'Stop Updating' : 'Start Updating'}
            </button>
          </div>
          
          {/* <div className="dashboard-item traffic-ratio">
            <h2>Traffic Ratio</h2>
            {ratio ? (
              <div>
                <p><span className="data-label">Anomalous:</span> <TruncatedData data={ratio.anomalous.toString()} /></p>
                <p><span className="data-label">Normal:</span> <TruncatedData data={ratio.normal.toString()} /></p>
                <p><span className="data-label">Ratio:</span> <TruncatedData data={ratio.ratio.toString()} /></p>
              </div>
            ) : (
              <p className="loading">Loading ratio...</p>
            )}
          </div> */}
          <div className="data-samples">
            <div className="dashboard-item">
              <h2>Raw Data Sample</h2>
              {rawSample ? (
                <div>
                  <p><span className="data-label">Key:</span> <TruncatedData data={rawSample.key} /></p>
                  <p><span className="data-label">Value:</span> <TruncatedData data={rawSample.value} /></p>
                </div>
              ) : (
                <p className="loading">Loading raw data sample...</p>
              )}
            </div>
            <div className="dashboard-item">
              <h2>Processed Data Sample</h2>
              {processedSample ? (
                <div>
                  <p><span className="data-label">Key:</span> <TruncatedData data={processedSample.key} /></p>
                  <p><span className="data-label">Value:</span> <TruncatedData data={processedSample.value} /></p>
                </div>
              ) : (
                <p className="loading">Loading processed data sample...</p>
              )}
            </div>
            {/* <div className="dashboard-item">
              <h2>Prediction Data Sample</h2>
              {predictionSample ? (
                <div>
                  <p><span className="data-label">Key:</span> <TruncatedData data={predictionSample.key} /></p>
                  <p><span className="data-label">Value:</span> <TruncatedData data={predictionSample.value} /></p>
                </div>
              ) : (
                <p className="loading">Loading prediction data sample...</p>
              )}
            </div> */}
          </div>
          <div className="dashboard-item train-model-card">
            <h2>Train Model</h2>
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
          </div>
        </div>
      </div>
    );
  };
  
  export default Dashboard;