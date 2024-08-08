import React, { useState, useRef } from 'react';
import TruncatedData from './TruncatedData';

const DataTable = ({ data, columns }) => {
  const [currentPacketIndex, setCurrentPacketIndex] = useState(0);

  if (!data || data.length === 0) {
    return <p className="loading">No data available...</p>;
  }

  const getValue = (row, key) => {
    if (key.includes('.')) {
      const keys = key.split('.');
      return keys.reduce((acc, part) => acc && acc[part], row);
    }
    return row[key];
  };

  const currentPacket = data[currentPacketIndex];

  const handlePreviousPacket = () => {
    if (currentPacketIndex > 0) {
      setCurrentPacketIndex(currentPacketIndex - 1);
    }
  };

  const handleNextPacket = () => {
    if (currentPacketIndex < data.length - 1) {
      setCurrentPacketIndex(currentPacketIndex + 1);
    }
  };

  return (
    <div className="data-table-container">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key}>{column.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr key={currentPacketIndex}>
            {columns.map((column) => (
              <td key={column.key} title={getValue(currentPacket, column.key)}>
                {column.render ? column.render(currentPacket) : <TruncatedData data={getValue(currentPacket, column.key)} />}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
      <div className="pagination-controls">
        <button className="themed_button" onClick={handlePreviousPacket} disabled={currentPacketIndex === 0}>Previous</button>
        <span>Packet {currentPacketIndex + 1} of {data.length}</span>
        <button className="themed_button" onClick={handleNextPacket} disabled={currentPacketIndex === data.length - 1}>Next</button>
      </div>
    </div>
  );
};

export default DataTable;