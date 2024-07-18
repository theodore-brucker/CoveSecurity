import React, { useState, useEffect } from 'react';

const Dashboard = () => {
  const [ratio, setRatio] = useState(null);
  const [health, setHealth] = useState(null);

  useEffect(() => {

    const fetchRatio = async () => {
      try {
        console.log('Backend URL:', "http://localhost:5000");
        console.log('Fetching ratio from:', "http://localhost:5000");
        const response = await fetch(`${"http://localhost:5000"}/ratio`);
        const data = await response.json();
        console.log('Ratio response:', data);
        setRatio(data);
      } catch (error) {
        console.error('Error fetching ratio:', error);
      }
    };

    const fetchHealth = async () => {
      try {
        console.log('Backend URL:', "http://localhost:5000");
        console.log('Fetching health check from:', "http://localhost:5000");
        const response = await fetch(`${"http://localhost:5000"}/health`);
        const data = await response.json();
        console.log('Health check response:', data);
        setHealth(data);
      } catch (error) {
        console.error('Error fetching health check:', error);
      }
    };

    fetchRatio();
    fetchHealth();
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
    </div>
  );
};

export default Dashboard;
