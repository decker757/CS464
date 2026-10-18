import { forgetVisits } from './visitedRoutes'

// [F-12] #341. Drops the last run's record of which pages were opened, so a
// route covered yesterday cannot cover itself today.

export default function clearVisitedRoutes(): void {
  forgetVisits()
}
