import React from 'react';

const PaginatedTable = ({ 
  data, 
  columns, 
  currentPage,
  totalPages,
  onPageChange,
  isLoading,
  renderCustomRow
}) => {
  if (isLoading) {
    return <p className="loading">Loading data...</p>;
  }

  if (!data || data.length === 0) {
    return <p>No data available</p>;
  }

  return (
    <>
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column, index) => (
              <th key={index}>{column.header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((item, rowIndex) => 
            renderCustomRow ? (
              renderCustomRow(item, rowIndex)
            ) : (
              <tr key={rowIndex}>
                {columns.map((column, colIndex) => (
                  <td key={colIndex}>{column.accessor(item)}</td>
                ))}
              </tr>
            )
          )}
        </tbody>
      </table>
      <div className="pagination-controls">
        <button onClick={() => onPageChange(currentPage - 1)} disabled={currentPage === 1}>Previous</button>
        <span>Page {currentPage} of {totalPages}</span>
        <button onClick={() => onPageChange(currentPage + 1)} disabled={currentPage === totalPages}>Next</button>
      </div>
    </>
  );
};

export default PaginatedTable;