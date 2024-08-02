import React from 'react';
import TruncatedData from './TruncatedData';

const DataTable = ({ data, columns }) => {
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
          {data.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {columns.map((column) => (
                <td key={column.key} title={getValue(row, column.key)}>
                  {column.render ? column.render(row) : <TruncatedData data={getValue(row, column.key)} />}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

export default DataTable;
