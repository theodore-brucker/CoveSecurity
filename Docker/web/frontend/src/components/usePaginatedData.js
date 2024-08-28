import { useState, useCallback, useEffect } from 'react';
import axios from 'axios';

const usePaginatedData = (fetchUrl, itemsPerPage = 10) => {
  const [data, setData] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);

  const fetchData = useCallback(async (page, filters = {}) => {
    console.log(`usePaginatedData - Fetching data: URL=${fetchUrl}, page=${page}, filters=`, filters);
    setLoading(true);
    setError(null);
    try {
      const params = { page, per_page: itemsPerPage, ...filters };
      const response = await axios.get(fetchUrl, { params });
      console.log('usePaginatedData - API response:', response.data);
      setData(response.data.data);
      setTotalPages(response.data.total_pages);
      setCurrentPage(response.data.current_page);
    } catch (err) {
      console.error('usePaginatedData - Error fetching data:', err);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [fetchUrl, itemsPerPage]);

  useEffect(() => {
    fetchData(currentPage);
  }, [currentPage, fetchData]);

  const handlePageChange = (newPage) => {
    console.log(`usePaginatedData - Changing page to ${newPage}`);
    setCurrentPage(newPage);
  };

  return { data, loading, error, currentPage, totalPages, handlePageChange, fetchData };
};

export default usePaginatedData;