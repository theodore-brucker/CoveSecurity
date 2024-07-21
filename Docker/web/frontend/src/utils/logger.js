// logger.js
const DEBUG_MODE = true; // Set to false in production

const logger = {
  log: (message, ...args) => {
    if (DEBUG_MODE) {
      console.log(message, ...args);
    }
  },
  error: (message, ...args) => {
    console.error(message, ...args);
  }
};

export default logger;
