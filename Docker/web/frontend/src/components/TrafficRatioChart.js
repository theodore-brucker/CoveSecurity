import React from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from 'recharts';

const TrafficRatioChart = ({ normal, anomalous }) => {
    const data = [
        { name: 'Normal', value: normal },
        { name: 'Anomalous', value: anomalous },
    ];

    return (
        <BarChart width={600} height={200} data={data}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="name" />
            <YAxis />
            <Tooltip />
            <Legend />
            <Bar dataKey="value" fill="#00CC66" />
        </BarChart>
    );
};

export default TrafficRatioChart;
