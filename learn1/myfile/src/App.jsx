import Topbar from "./components/topbar/Topbar"
import Intro from "./components/intro/Intro"
import Portpolio from "./components/portfolio/Portpolio"
import Works from "./components/works/Works"
import Testimonial from "./components/Testimonial/Testimonial"
import Contact from "./components/contact/Contact"
import "./app.scss"


function App() {
  return (
    <div className="app">
      <Topbar/>
      <div className="sections">
        <Intro/>
        <Portpolio/>
        <Works/>
        <Testimonial/>
        <Contact/>
      </div>
    </div>
    
  );
}

export default App;
