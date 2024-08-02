export const truncateString = (str, maxLength) => {
    if (str == null) {
      return '';
    }
    if (typeof str !== 'string') {
      try {
        str = JSON.stringify(str);
      } catch (e) {
        return ''; // return empty string if data cannot be stringified
      }
    }
    if (!str || str.length <= maxLength) return str;
    return str.slice(0, maxLength) + '...';
  };