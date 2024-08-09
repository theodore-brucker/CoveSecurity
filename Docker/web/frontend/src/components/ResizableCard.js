import React, { useState, useEffect, useRef } from 'react';

const ResizableCard = ({ title, children }) => {
  const [isVisible, setIsVisible] = useState(true);
  const [content, setContent] = useState(children);
  const contentRef = useRef(children);

  useEffect(() => {
    if (JSON.stringify(children) !== JSON.stringify(contentRef.current)) {
      contentRef.current = children;
      setContent(children);
    }
  }, [children]);

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
          {content}
        </div>
      )}
    </div>
  );
};

export default ResizableCard;