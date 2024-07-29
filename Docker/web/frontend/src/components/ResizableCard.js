import React, { useState, useRef, useEffect } from 'react';

const ResizableCard = ({ title, children, initialHeight = 300, initialWidth = 300, onDragStart, onDragEnd, index }) => {
  const [isVisible, setIsVisible] = useState(true);
  const [height, setHeight] = useState(initialHeight);
  const [width, setWidth] = useState(initialWidth);
  const cardRef = useRef(null);
  const [isResizing, setIsResizing] = useState(false);
  const [resizeDirection, setResizeDirection] = useState(null);
  const [startPos, setStartPos] = useState({ x: 0, y: 0 });
  const [startSize, setStartSize] = useState({ width: 0, height: 0 });

  useEffect(() => {
    const handleMouseMove = (e) => {
      if (isResizing) {
        e.preventDefault();
        const dx = e.clientX - startPos.x;
        const dy = e.clientY - startPos.y;
        
        if (resizeDirection === 'bottom' || resizeDirection === 'corner') {
          const newHeight = Math.max(100, startSize.height + dy);
          setHeight(newHeight);
        }
        if (resizeDirection === 'right' || resizeDirection === 'corner') {
          const newWidth = Math.max(200, startSize.width + dx);
          setWidth(newWidth);
        }
      }
    };

    const handleMouseUp = () => {
      setIsResizing(false);
      setResizeDirection(null);
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isResizing, startPos, startSize, resizeDirection]);

  const handleResizeStart = (direction) => (e) => {
    e.preventDefault();
    setIsResizing(true);
    setResizeDirection(direction);
    setStartPos({ x: e.clientX, y: e.clientY });
    setStartSize({ width, height });
  };

  const toggleVisibility = () => {
    setIsVisible(!isVisible);
  };

  const handleDragStart = (e) => {
    onDragStart(index, e);
  };

  const handleDragEnd = () => {
    onDragEnd();
  };

  return (
    <div 
      ref={cardRef}
      className={`resizable-card ${isVisible ? '' : 'collapsed'}`} 
      style={{ height: isVisible ? height : 'auto', width: width }}
      draggable
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
    >
      <div className="card-header" onClick={toggleVisibility}>
        <h2>{title}</h2>
        <span className="toggle-icon">{isVisible ? '▼' : '▶'}</span>
      </div>
      {isVisible && (
        <>
          <div className="card-content">
            {children}
          </div>
          <div 
            className="resize-handle bottom" 
            onMouseDown={handleResizeStart('bottom')}
          />
          <div 
            className="resize-handle right" 
            onMouseDown={handleResizeStart('right')}
          />
          <div 
            className="resize-handle corner" 
            onMouseDown={handleResizeStart('corner')}
          />
        </>
      )}
    </div>
  );
};

export default ResizableCard;
