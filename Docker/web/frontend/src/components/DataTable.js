import React, { useState } from 'react';
import TruncatedData from './TruncatedData';

const DataTable = ({ data, columns, isMultiSequence = false }) => {
  const [currentSequenceIndex, setCurrentSequenceIndex] = useState(0);

  if (!data || data.length === 0) {
    return <p className="loading">No data available...</p>;
  }

  const currentSequence = data[currentSequenceIndex];

  const handlePreviousSequence = () => {
    if (currentSequenceIndex > 0) {
      setCurrentSequenceIndex(currentSequenceIndex - 1);
    }
  };

  const handleNextSequence = () => {
    if (currentSequenceIndex < data.length - 1) {
      setCurrentSequenceIndex(currentSequenceIndex + 1);
    }
  };

  return (
    <div className="data-table-container">
      <h3>Sequence ID: {currentSequence.id}</h3>
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key}>{column.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {currentSequence.sequence.map((packet, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column.key}>
                  {column.render ? 
                    column.render(packet, currentSequence.human_readable[index]) : 
                    <TruncatedData data={packet && packet[column.index] ? packet[column.index] : '\u00A0'} />
                  }
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {isMultiSequence && (
        <div className="pagination-controls">
          <button className="themed_button" onClick={handlePreviousSequence} disabled={currentSequenceIndex === 0}>Previous</button>
          <span>Sequence {currentSequenceIndex + 1} of {data.length}</span>
          <button className="themed_button" onClick={handleNextSequence} disabled={currentSequenceIndex === data.length - 1}>Next</button>
        </div>
      )}
    </div>
  );
};

export default DataTable;

