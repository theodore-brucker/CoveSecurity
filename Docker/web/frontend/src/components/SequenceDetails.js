import React from 'react';

const SequenceDetails = ({ sequence, onBack }) => {
  return (
    <div className="sequence-details">
      <button className="themed_button" onClick={onBack}>Back to List</button>
      <h3>Sequence Details</h3>
      <p><strong>ID:</strong> {sequence._id}</p>
      <p><strong>Reconstruction Error:</strong> {sequence.reconstruction_error.toFixed(4)}</p>
      <h4>Human Readable Data:</h4>
      <pre>{JSON.stringify(sequence.human_readable, null, 2)}</pre>
      <h4>Sequence Data:</h4>
      <pre>{JSON.stringify(sequence.sequence, null, 2)}</pre>
    </div>
  );
};

export default SequenceDetails;
