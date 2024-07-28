import React, { useState } from 'react';

const ResizableCard = ({ title, children, initialHeight = 300 }) => {
  const [height, setHeight] = useState(initialHeight);

  const handleResize = (newHeight) => {
    setHeight(Math.max(100, newHeight)); // Minimum height of 100px
  };

  return (
    <div className="dashboard-item" style={{ height: `${height}px`, overflow: 'auto' }}>
      <h2>{title}</h2>
      {children}
      <div className="resize-handle">
        <button onClick={() => handleResize(height - 50)}>-</button>
        <button onClick={() => handleResize(height + 50)}>+</button>
      </div>
    </div>
  );
};

export default ResizableCard;