import React, { useState } from 'react';
import PaginatedTable from './PaginatedTable';
import ResizableCard from './ResizableCard';
import usePaginatedData from './usePaginatedData';

const MongoDataCard = () => {
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');

  const { 
    data, 
    loading, 
    error, 
    currentPage, 
    totalPages, 
    handlePageChange, 
    fetchData 
  } = usePaginatedData('http://localhost:5000/api/mongodb_data');

  const columns = [
    { header: 'ID', accessor: item => item._id },
    { header: 'Timestamp', accessor: item => new Date(item.timestamp.$date).toLocaleString() }
  ];

  const handleFetchData = () => {
    fetchData(1, { start_date: startDate, end_date: endDate });
  };

  return (
    <div className="dashboard-item">
      <ResizableCard title="MongoDB Data Sample">
        <div className="date-selection">
          <label>
            Start Date:
            <input 
              type="datetime-local" 
              value={startDate} 
              onChange={(e) => setStartDate(e.target.value)} 
            />
          </label>
          <label>
            End Date:
            <input 
              type="datetime-local" 
              value={endDate} 
              onChange={(e) => setEndDate(e.target.value)} 
            />
          </label>
          <button className="themed_button" onClick={handleFetchData}>
            Fetch Data
          </button>
        </div>
        {error && <p className="error">{error}</p>}
        <PaginatedTable
          data={data}
          columns={columns}
          currentPage={currentPage}
          totalPages={totalPages}
          onPageChange={handlePageChange}
          isLoading={loading}
        />
      </ResizableCard>
    </div>
  );
};

export default MongoDataCard;