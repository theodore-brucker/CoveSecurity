import React, { useState, useRef } from 'react';
import { truncateString } from '../utils/stringUtils';

const TruncatedData = ({ data, maxLength = 12 }) => {
  const [isExpanded, setIsExpanded] = useState(false);
  const [showTooltip, setShowTooltip] = useState(false);
  const dataRef = useRef(null);

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

  const handleMouseEnter = () => {
    if (dataRef.current.offsetWidth < dataRef.current.scrollWidth) {
      setShowTooltip(true);
    }
  };

  const handleMouseLeave = () => {
    setShowTooltip(false);
  };

  return (
    <div
      className="truncated-data"
      ref={dataRef}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      <span className="data-value">
        {isExpanded ? displayData : truncateString(displayData, maxLength)}
      </span>
      {showTooltip && (
        <div className="tooltip">
          {displayData}
        </div>
      )}
      {displayData.length > maxLength && (
        <button 
          className="toggle-button" 
          onClick={() => setIsExpanded(!isExpanded)}
        >
          {isExpanded ? 'Show Less' : 'Show More'}
        </button>
      )}
    </div>
  );
};

export default TruncatedData;