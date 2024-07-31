import React from 'react';

const ResizableCard = ({ title, children }) => {
  const [isVisible, setIsVisible] = React.useState(true);

  const toggleVisibility = () => {
    setIsVisible(!isVisible);
  };

  return (
    <div className={`resizable-card ${isVisible ? '' : 'collapsed'}`}>
      <div className="card-header" onClick={toggleVisibility}>
        <h2>{title}</h2>
        <span className="toggle-icon">{isVisible ? '▼' : '▶'}</span>
      </div>
      {isVisible && (
        <div className="card-content">
          {children}
        </div>
      )}
    </div>
  );
};

export default ResizableCard;

