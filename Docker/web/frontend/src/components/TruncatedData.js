// src/components/TruncatedData.js
import React, { useState } from 'react';
import { truncateString } from '../utils/stringUtils';

const TruncatedData = ({ data, maxLength = 12 }) => {
  const [isExpanded, setIsExpanded] = useState(false);

  console.log("TruncatedData received data:", data, "Type:", typeof data);

  let displayData;
  try {
    displayData = data == null ? '' : 
      (typeof data !== 'string' ? JSON.stringify(data) : data);
  } catch (error) {
    console.error("Error processing data in TruncatedData:", error);
    displayData = 'Error processing data';
  }

  console.log("TruncatedData displayData:", displayData);

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

export default TruncatedData;
