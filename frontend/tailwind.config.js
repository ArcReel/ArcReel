/** @type {import('tailwindcss').Config} */
export default {
    darkMode: "class",
    content: [
        "./index.html",
        "./src/**/*.{js,ts,jsx,tsx}",
    ],
    theme: {
        extend: {
            colors: {
                ink: {
                    950: "#060a0f",
                    900: "#0b1320",
                    800: "#132034",
                    700: "#22344f",
                },
                neon: {
                    500: "#11d3ac",
                    400: "#34e7c5",
                    300: "#6af2d8",
                },
                amberx: {
                    500: "#f0a93e",
                    400: "#f6be67",
                    300: "#ffd892",
                },
            },
            boxShadow: {
                glow: "0 0 0 1px rgba(17, 211, 172, 0.28), 0 18px 40px rgba(8, 15, 25, 0.45)",
            },
        },
    },
    plugins: [],
};
