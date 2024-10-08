// AnomalousSequencesCard.js
import React, { useState, useEffect } from 'react';
import PaginatedTable from './PaginatedTable';
import ResizableCard from './ResizableCard';
import usePaginatedData from './usePaginatedData';
import SequenceDetails from './SequenceDetails';

const AnomalousSequencesCard = () => {
  const [selectedSequence, setSelectedSequence] = useState(null);

  const { 
    data, 
    loading, 
    error, 
    currentPage, 
    totalPages, 
    handlePageChange, 
    fetchData 
  } = usePaginatedData('http://localhost:5000/api/anomalous_sequences');

  useEffect(() => {
    console.log('AnomalousSequencesCard - Data updated:', data);
    console.log('AnomalousSequencesCard - Current page:', currentPage);
    console.log('AnomalousSequencesCard - Total pages:', totalPages);
  }, [data, currentPage, totalPages]);

  const handleMarkAsFalsePositive = async (id) => {
    try {
      const response = await fetch('http://localhost:5000/mark_as_normal', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ _id: id }),
      });

      if (!response.ok) {
        throw new Error('Failed to mark sequence as false positive');
      }

      console.log(`Sequence ${id} marked as false positive`);
      // Refresh the data to reflect the changes
      fetchData(currentPage);
    } catch (error) {
      console.error('Error marking sequence as false positive:', error);
      // You might want to set an error state here and display it to the user
    }
  };

  const columns = [
    { header: 'ID', accessor: item => item._id },
    { header: 'Reconstruction Error', accessor: item => item.reconstruction_error ? item.reconstruction_error.toFixed(4) : 'N/A' },
    { header: 'Familiarity', accessor: item => item.familiarity !== undefined ? item.familiarity.toFixed(4) : 'N/A' },
    { 
      header: 'Actions', 
      accessor: item => (
        <div className="button-container">
          <button className="themed_button mark-as-normal-button" onClick={() => setSelectedSequence(item)}>View</button>
          <button className="themed_button mark-as-normal-button" onClick={() => handleMarkAsFalsePositive(item._id)}>Mark FP</button>
        </div>
      )
    }
  ];

  const handleRefresh = () => {
    console.log('AnomalousSequencesCard - Refreshing data');
    fetchData(1);
  };

  if (selectedSequence) {
    return (
      <SequenceDetails 
        sequence={selectedSequence} 
        onBack={() => setSelectedSequence(null)} 
      />
    );
  }

  return (
    <div className="dashboard-item">
      <ResizableCard title="Anomalous Sequences">
        <button className="themed_button" onClick={handleRefresh}>
          Refresh
        </button>
        {error && <p className="error">{error}</p>}
        {loading && <p>Loading...</p>}
        {!loading && data.length === 0 && <p>No anomalous sequences found.</p>}
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

export default AnomalousSequencesCard;