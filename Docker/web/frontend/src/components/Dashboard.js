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

  const getHealthStatusClass = (status) => {
    return status === 'OK' ? 'status-healthy' : 'status-unhealthy';
  };

  useEffect(() => {
    logger.log('Fetching ratio data...');
    const fetchRatio = async () => {
      try {
        const response = await fetch("http://localhost:5000/ratio");
        const data = await response.json();
        logger.log('Fetched ratio data:', data);
        setRatio(data);
      } catch (error) {
        logger.error('Error fetching ratio:', error);
      }
    };

    logger.log('Fetching health check data...');
    const fetchHealth = async () => {
      try {
        const response = await fetch("http://localhost:5000/health");
        const data = await response.json();
        logger.log('Fetched health check data:', data);
        setHealth(data);
      } catch (error) {
        logger.error('Error fetching health check:', error);
      }
    };

    logger.log('Fetching raw data sample...');
    const fetchRawSample = async () => {
      try {
        const response = await fetch("http://localhost:5000/raw_sample");
        const data = await response.json();
        logger.log('Fetched raw data sample:', data);
        setRawSample(data);
      } catch (error) {
        logger.error('Error fetching raw sample:', error);
      }
    };

    logger.log('Fetching processed data sample...');
    const fetchProcessedSample = async () => {
      try {
        const response = await fetch("http://localhost:5000/processed_sample");
        const data = await response.json();
        logger.log('Fetched processed data sample:', data);
        setProcessedSample(data);
      } catch (error) {
        logger.error('Error fetching processed sample:', error);
      }
    };

    logger.log('Fetching prediction data sample...');
    const fetchPredictionSample = async () => {
      try {
        const response = await fetch("http://localhost:5000/prediction_sample");
        const data = await response.json();
        logger.log('Fetched prediction data sample:', data);
        setPredictionSample(data);
      } catch (error) {
        logger.error('Error fetching prediction sample:', error);
      }
    };

    fetchRatio();
    fetchHealth();
    fetchRawSample();
    fetchProcessedSample();
    fetchPredictionSample();
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
        <div className="dashboard-item traffic-ratio">
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
        </div>
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
          <div className="dashboard-item">
            <h2>Prediction Data Sample</h2>
            {predictionSample ? (
              <div>
                <p><span className="data-label">Key:</span> <TruncatedData data={predictionSample.key} /></p>
                <p><span className="data-label">Value:</span> <TruncatedData data={predictionSample.value} /></p>
              </div>
            ) : (
              <p className="loading">Loading prediction data sample...</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;