import React from "react"
import ReactDOM from "react-dom/client"
import App from "./App.jsx"
import "./styles/index.css"

const storedTheme = String(localStorage.getItem("ui_theme") || "").toLowerCase()
if (storedTheme === "light" || storedTheme === "dark") {
  document.documentElement.setAttribute("data-theme", storedTheme)
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
