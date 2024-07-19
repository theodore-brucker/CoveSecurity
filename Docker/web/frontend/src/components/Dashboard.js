import React, { useState, useEffect } from 'react';

const Dashboard = () => {
  const [ratio, setRatio] = useState(null);
  const [health, setHealth] = useState(null);
  const [rawSample, setRawSample] = useState(null);
  const [processedSample, setProcessedSample] = useState(null);
  const [predictionSample, setPredictionSample] = useState(null);

  useEffect(() => {
    const fetchRatio = async () => {
      try {
        const response = await fetch("http://localhost:5000/ratio");
        const data = await response.json();
        setRatio(data);
      } catch (error) {
        console.error('Error fetching ratio:', error);
      }
    };

    const fetchHealth = async () => {
      try {
        const response = await fetch("http://localhost:5000/health");
        const data = await response.json();
        setHealth(data);
      } catch (error) {
        console.error('Error fetching health check:', error);
      }
    };

    const fetchRawSample = async () => {
      try {
        const response = await fetch("http://localhost:5000/raw_sample");
        const data = await response.json();
        setRawSample(data);
      } catch (error) {
        console.error('Error fetching raw sample:', error);
      }
    };

    const fetchProcessedSample = async () => {
      try {
        const response = await fetch("http://localhost:5000/processed_sample");
        const data = await response.json();
        setProcessedSample(data);
      } catch (error) {
        console.error('Error fetching processed sample:', error);
      }
    };

    const fetchPredictionSample = async () => {
      try {
        const response = await fetch("http://localhost:5000/prediction_sample");
        const data = await response.json();
        setPredictionSample(data);
      } catch (error) {
        console.error('Error fetching prediction sample:', error);
      }
    };

    fetchRatio();
    fetchHealth();
    fetchRawSample();
    fetchProcessedSample();
    fetchPredictionSample();
  }, []);

  return (
    <div>
      <h1>Kafka Dashboard</h1>
      <div>
        <h2>Health Check</h2>
        {health ? <p>{health.status}</p> : <p>Loading health status...</p>}
      </div>
      <div>
        <h2>Ratio of Anomalous vs Normal Traffic</h2>
        {ratio ? (
          <div>
            <p>Anomalous: {ratio.anomalous}</p>
            <p>Normal: {ratio.normal}</p>
            <p>Ratio: {ratio.ratio}</p>
          </div>
        ) : (
          <p>Loading ratio...</p>
        )}
      </div>
      <div>
        <h2>Raw Data Sample</h2>
        {rawSample ? (
          <div>
            <p>Key: {rawSample.key}</p>
            <p>Value: {rawSample.value}</p>
          </div>
        ) : (
          <p>Loading raw data sample...</p>
        )}
      </div>
      <div>
        <h2>Processed Data Sample</h2>
        {processedSample ? (
          <div>
            <p>Key: {processedSample.key}</p>
            <p>Value: {processedSample.value}</p>
          </div>
        ) : (
          <p>Loading processed data sample...</p>
        )}
      </div>
      <div>
        <h2>Prediction Data Sample</h2>
        {predictionSample ? (
          <div>
            <p>Key: {predictionSample.key}</p>
            <p>Value: {predictionSample.value}</p>
          </div>
        ) : (
          <p>Loading prediction data sample...</p>
        )}
      </div>
    </div>
  );
};

export default Dashboard;
